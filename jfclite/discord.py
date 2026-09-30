"""Zeigt in Discord an, was gerade läuft ("Rich Presence").

Spricht direkt Discords lokale IPC-Schnittstelle (Unix-Socket, JSON-Frames) -
keine zusätzliche Bibliothek. Läuft in einem eigenen Hintergrund-Thread,
damit ein hängendes oder fehlendes Discord die TUI nie bremst. Gesendet wird
nur, wenn sich etwas ändert (Titel, Pause, Spulen) - den Fortschrittsbalken
berechnet Discord selbst aus Start-/Endzeit - und höchstens alle 4 s
(Discord erlaubt 5 Updates pro 20 s).
"""

from __future__ import annotations

import glob
import json
import os
import struct
import threading
import time
import uuid
from typing import Iterator, Optional

from . import STATE_DIR, ipc
from .client import Track

OP_HANDSHAKE, OP_FRAME, OP_CLOSE, OP_PING, OP_PONG = range(5)
TYPE_PLAYING, TYPE_LISTENING = 0, 2

LOG_PATH = str(STATE_DIR / "discord.log")


class _InvalidClientId(Exception):
    """Discord kennt die Application-ID nicht - erneut Versuchen ist sinnlos."""


def _socket_paths() -> Iterator[str]:
    if ipc.WINDOWS:
        # Discord, Vesktop, Equibop, ... - Existenz lässt sich bei Pipes nicht
        # billig prüfen, ein fehlgeschlagener Verbindungsversuch ist aber schnell.
        yield from (rf"\\.\pipe\discord-ipc-{i}" for i in range(10))
        return
    base = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp"
    for pattern in (
        "discord-ipc-[0-9]",  # normales Paket
        "app/*/discord-ipc-[0-9]",  # Flatpak (offizielles Discord)
        ".flatpak/*/xdg-run/discord-ipc-[0-9]",  # Flatpak (Equibop, Vesktop, ...)
        "snap.*/discord-ipc-[0-9]",  # Snap
    ):
        yield from sorted(glob.glob(os.path.join(base, pattern)))


def _text(value: str) -> str:
    """Discord lehnt Textfelder unter 2 oder über 128 Zeichen ab."""
    value = value.strip()
    if len(value) < 2:
        value = value.ljust(2, "⠀")  # unsichtbares Zeichen, wird nicht entfernt
    return value[:128]


class DiscordPresence:
    MIN_INTERVAL = 4.0  # Sekunden zwischen zwei Updates
    RETRY_INTERVAL = 30.0  # Discord nicht erreichbar -> so oft neu versuchen

    def __init__(self, client_id: str, cover_base_url: Optional[str] = None):
        self._client_id = str(client_id)
        self._cover_base = cover_base_url.rstrip("/") if cover_base_url else None
        self._type = TYPE_LISTENING  # "Hört ..."; Fallback "Spielt ...", s. _send()
        self._sock = None  # socket.socket oder ipc._WindowsPipe
        self._cond = threading.Condition()
        self._activity: Optional[dict] = None
        self._version = 0  # zählt Änderungen von _activity
        self._key: Optional[tuple] = None
        self._closing = False
        self._disabled = False
        self._last_log = ""
        self._thread = threading.Thread(target=self._run, name="discord", daemon=True)
        self._thread.start()

    def update(self, track: Track, pos: float, dur: float, paused: bool) -> None:
        """Wird jede Sekunde aufgerufen; gibt nur echte Änderungen weiter."""
        dur = dur or track.duration_seconds
        start = round(time.time() - pos)
        key = (track.id, paused, round(dur), None if paused else start)
        old = self._key
        if old and old[:3] == key[:3] and (paused or abs(old[3] - start) <= 2):
            return  # nur Rundungsrauschen, kein Spulen
        self._key = key

        activity: dict = {
            "details": _text(track.title),
            "state": _text(("⏸ " if paused else "") + track.artist),
        }
        if not paused and dur:
            activity["timestamps"] = {"start": start * 1000, "end": round(start + dur) * 1000}
        if self._cover_base:
            # Jellyfin liefert Bilder ohne Anmeldung aus - die Adresse enthält
            # also weder Passwort noch Token (Discord zeigt sie öffentlich).
            item = track.album_id or track.id
            assets = {
                "large_image": f"{self._cover_base}/Items/{item}/Images/Primary"
                "?fillWidth=300&fillHeight=300&quality=85"
            }
            if track.album:
                assets["large_text"] = _text(track.album)
            activity["assets"] = assets
        self._set(activity)

    def clear(self) -> None:
        self._key = None
        self._set(None)

    def close(self) -> None:
        """Beim Beenden: Anzeige in Discord entfernen, Verbindung schließen."""
        with self._cond:
            self._closing = True
            self._cond.notify()
        self._thread.join(timeout=3)

    def _set(self, activity: Optional[dict]) -> None:
        with self._cond:
            self._activity = activity
            self._version += 1
            self._cond.notify()

    def _run(self) -> None:
        sent_version = 0
        next_try = 0.0
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._closing or self._version != sent_version)
                if self._closing:
                    break
                delay = next_try - time.monotonic()
                if delay > 0:
                    self._cond.wait_for(lambda: self._closing, timeout=delay)
                    continue
                activity, version = self._activity, self._version
            if self._send(activity):
                sent_version = version
                next_try = time.monotonic() + self.MIN_INTERVAL
            elif self._disabled:
                return
            else:
                next_try = time.monotonic() + self.RETRY_INTERVAL
        if self._sock:
            self._send(None)
            self._disconnect()

    # --- IPC-Protokoll ---------------------------------------------------

    def _send(self, activity: Optional[dict]) -> bool:
        try:
            if self._sock is None:
                self._connect()
            reply = self._set_activity(activity)
            if reply.get("evt") == "ERROR" and activity and self._type == TYPE_LISTENING:
                # ältere Discord-Versionen erlauben per IPC nur "Spielt ..."
                self._type = TYPE_PLAYING
                reply = self._set_activity(activity)
            if reply.get("evt") == "ERROR":
                self._log(f"Discord hat die Anzeige abgelehnt: {reply.get('data')}")
                return False
            return True
        except _InvalidClientId as exc:
            self._log(f"{exc} - Discord-Anzeige ist bis zum nächsten Start aus.")
            self._disabled = True
            return False
        except (OSError, ValueError, struct.error) as exc:
            if self._sock is not None:
                self._log(f"Verbindung zu Discord verloren: {exc}")
            self._disconnect()
            return False

    def _set_activity(self, activity: Optional[dict]) -> dict:
        if activity is not None:
            activity = {**activity, "type": self._type}
        return self._request("SET_ACTIVITY", {"pid": os.getpid(), "activity": activity})

    def _connect(self) -> None:
        for path in _socket_paths():
            try:
                self._sock = ipc.connect(path, timeout=3)
            except OSError:
                continue
            break
        else:
            raise OSError("Discord läuft nicht")
        try:
            self._write(OP_HANDSHAKE, {"v": 1, "client_id": self._client_id})
            op, data = self._read()
        except (OSError, ValueError, struct.error):
            self._disconnect()
            raise
        if op == OP_CLOSE or data.get("evt") != "READY":
            self._disconnect()
            if data.get("code") == 4000:
                raise _InvalidClientId(
                    f"Discord kennt die Application-ID {self._client_id} nicht"
                    " (discord_client_id in der config.json prüfen)"
                )
            self._log(f"Discord-Anmeldung fehlgeschlagen: {data}")
            raise OSError("Discord-Anmeldung fehlgeschlagen")

    def _request(self, cmd: str, args: dict) -> dict:
        self._write(OP_FRAME, {"cmd": cmd, "args": args, "nonce": uuid.uuid4().hex})
        while True:
            op, data = self._read()
            if op == OP_PING:
                self._write(OP_PONG, data)
            elif op == OP_CLOSE:
                raise OSError(data.get("message", "Discord hat die Verbindung beendet"))
            elif data.get("cmd") == cmd:
                return data

    def _write(self, op: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._sock.sendall(struct.pack("<II", op, len(body)) + body)

    def _read(self) -> tuple[int, dict]:
        op, length = struct.unpack("<II", self._recv_exact(8))
        return op, json.loads(self._recv_exact(length).decode("utf-8"))

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise OSError("Discord hat die Verbindung geschlossen")
            buf += chunk
        return buf

    def _disconnect(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _log(self, message: str) -> None:
        if message == self._last_log:
            return  # dieselbe Meldung nicht alle 30 s wiederholen
        self._last_log = message
        try:
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as log:
                log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
        except OSError:
            pass
