"""Zeigt in Discord an, was gerade läuft ("Rich Presence").

Spricht direkt Discords lokale IPC-Schnittstelle (Unix-Socket, JSON-Frames) -
keine zusätzliche Bibliothek. Die Anzeige geht an jedes laufende Discord-
Programm (Discord, Equibop, Vesktop, ...); später gestartete findet ein
Suchlauf alle 30 s. Läuft in einem eigenen Hintergrund-Thread, damit ein
hängendes oder fehlendes Discord die TUI nie bremst. Gesendet wird nur, wenn
sich etwas ändert (Titel, Pause, Spulen) - den Fortschrittsbalken berechnet
Discord selbst aus Start-/Endzeit - und höchstens alle 4 s (Discord erlaubt
5 Updates pro 20 s).
"""

from __future__ import annotations

import glob
import json
import os
import re
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


def _program(path: str) -> str:
    """Name fürs Log: die Flatpak-/Snap-Kennung (org.equicord.equibop,
    snap.discord), sonst der Name des Sockets (discord-ipc-0)."""
    match = re.search(r"(?:^|/)(?:\.flatpak|app)/([^/]+)/|(?:^|/)(snap\.[^/]+)/", path)
    return (match.group(1) or match.group(2)) if match else os.path.basename(path)


def _text(value: str) -> str:
    """Discord lehnt Textfelder unter 2 oder über 128 Zeichen ab."""
    value = value.strip()
    if len(value) < 2:
        value = value.ljust(2, "⠀")  # unsichtbares Zeichen, wird nicht entfernt
    return value[:128]


class _Connection:
    """Verbindung zu einem Discord-Programm. Wird nur vom Discord-Thread benutzt."""

    def __init__(self, sock, name: str):
        self.sock = sock  # socket.socket oder ipc._WindowsPipe
        self.name = name
        self.type = TYPE_LISTENING  # "Hört ..."; Fallback "Spielt ...", s. DiscordPresence._send()
        self.sent = 0  # Version der Anzeige, die hier zuletzt angekommen ist
        self.next_send = 0.0  # frühestens dann das nächste Update (time.monotonic())

    def handshake(self, client_id: str) -> None:
        try:
            self._write(OP_HANDSHAKE, {"v": 1, "client_id": client_id})
            op, data = self._read()
        except (OSError, ValueError, struct.error):
            self.close()
            raise
        if op == OP_CLOSE or data.get("evt") != "READY":
            self.close()
            if data.get("code") == 4000:
                raise _InvalidClientId
            raise OSError(f"Anmeldung abgelehnt: {data}")

    def set_activity(self, activity: Optional[dict]) -> dict:
        if activity is not None:
            activity = {**activity, "type": self.type}
        return self._request("SET_ACTIVITY", {"pid": os.getpid(), "activity": activity})

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

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
        self.sock.sendall(struct.pack("<II", op, len(body)) + body)

    def _read(self) -> tuple[int, dict]:
        op, length = struct.unpack("<II", self._recv_exact(8))
        return op, json.loads(self._recv_exact(length).decode("utf-8"))

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise OSError("Discord hat die Verbindung geschlossen")
            buf += chunk
        return buf


class DiscordPresence:
    MIN_INTERVAL = 4.0  # Sekunden zwischen zwei Updates
    RETRY_INTERVAL = 30.0  # so oft nach (neu gestarteten) Discord-Programmen suchen

    def __init__(self, client_id: str, cover_base_url: Optional[str] = None):
        self._client_id = str(client_id)
        self._cover_base = cover_base_url.rstrip("/") if cover_base_url else None
        # Nur der Discord-Thread fasst die Verbindungen an
        self._conns: dict[str, _Connection] = {}  # Socket (ohne Symlinks) -> Verbindung
        self._rejected: set[str] = set()  # Sockets, deren Programm die ID nicht kennt
        self._cond = threading.Condition()
        self._activity: Optional[dict] = None
        self._version = 0  # zählt Änderungen von _activity
        self._key: Optional[tuple] = None
        self._closing = False
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
        if self._cover_base and track.cover_id:
            # Jellyfin liefert Bilder ohne Anmeldung aus - die Adresse enthält
            # also weder Passwort noch Token (Discord zeigt sie öffentlich).
            assets = {
                "large_image": f"{self._cover_base}/Items/{track.cover_id}/Images/Primary"
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
        """Beim Beenden: Anzeige in Discord entfernen, Verbindungen schließen."""
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
        next_scan = 0.0
        while True:
            with self._cond:
                while not self._closing:
                    wake = self._next_wake(next_scan)
                    delay = None if wake is None else wake - time.monotonic()
                    if delay is not None and delay <= 0:
                        break
                    self._cond.wait(delay)
                if self._closing:
                    break
                activity, version = self._activity, self._version
            if activity is not None and time.monotonic() >= next_scan:
                self._scan()
                next_scan = time.monotonic() + self.RETRY_INTERVAL
            for path, conn in list(self._conns.items()):
                if conn.sent != version and time.monotonic() >= conn.next_send:
                    self._send(path, conn, activity, version)
        for conn in self._conns.values():
            try:
                conn.set_activity(None)
            except (OSError, ValueError, struct.error):
                pass
            conn.close()

    def _next_wake(self, next_scan: float) -> Optional[float]:
        """Wann es wieder etwas zu senden oder zu suchen gibt (time.monotonic()),
        None = erst, wenn sich die Anzeige ändert."""
        times = [c.next_send for c in self._conns.values() if c.sent != self._version]
        if self._activity is not None:
            times.append(next_scan)
        return min(times, default=None)

    def _scan(self) -> None:
        """Verbindet sich mit jedem laufenden Discord-Programm, mit dem noch
        keine Verbindung besteht."""
        for path in _socket_paths():
            # Symlinks (z.B. discord-ipc-0 -> app/.../discord-ipc-0) nur einmal
            real = path if ipc.WINDOWS else os.path.realpath(path)
            if real in self._conns or real in self._rejected:
                continue
            try:
                sock = ipc.connect(path, timeout=3)
            except OSError:
                continue  # Programm läuft nicht (mehr), nur der Socket ist übrig
            conn = _Connection(sock, _program(path))
            try:
                conn.handshake(self._client_id)
            except _InvalidClientId:
                self._log(
                    f"{conn.name} kennt die Application-ID {self._client_id} nicht"
                    " (discord_client_id in der config.json prüfen) - dort bis zum"
                    " nächsten Start keine Anzeige."
                )
                self._rejected.add(real)
                continue
            except (OSError, ValueError, struct.error) as exc:
                self._log(f"Anmeldung bei {conn.name} fehlgeschlagen: {exc}")
                continue
            self._conns[real] = conn

    def _send(self, path: str, conn: _Connection, activity: Optional[dict], version: int) -> None:
        try:
            reply = conn.set_activity(activity)
            if reply.get("evt") == "ERROR" and activity and conn.type == TYPE_LISTENING:
                # ältere Discord-Versionen erlauben per IPC nur "Spielt ..."
                conn.type = TYPE_PLAYING
                reply = conn.set_activity(activity)
        except (OSError, ValueError, struct.error) as exc:
            # beim nächsten Suchlauf neu verbinden, falls das Programm noch läuft
            self._log(f"Verbindung zu {conn.name} verloren: {exc}")
            conn.close()
            del self._conns[path]
            return
        if reply.get("evt") == "ERROR":
            self._log(f"{conn.name} hat die Anzeige abgelehnt: {reply.get('data')}")
            conn.next_send = time.monotonic() + self.RETRY_INTERVAL
            return
        conn.sent = version
        conn.next_send = time.monotonic() + self.MIN_INTERVAL

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
