"""Steuert eine einzelne mpv-Instanz über den JSON-IPC-Socket.

mpv macht die eigentliche Dekodierung/Wiedergabe (effizient, hardwarebeschleunigt
wo möglich) - wir schicken nur schlanke JSON-Kommandos. Kein zusätzliches
Audio-Backend, keine eigene Dekodierung -> minimaler CPU-/RAM-Verbrauch hier.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Any, Iterable, Optional

from . import CACHE_DIR, STATE_DIR, ipc


def find_mpv(configured: Optional[str] = None) -> Optional[str]:
    """Pfad zu mpv, oder None. Mit `configured` (mpv_path aus der Config,
    mpv selbst oder sein Ordner) nur dort. Sonst der PATH, unter Windows
    vorher der Programmordner (für ein einfach daneben entpacktes mpv) und
    danach die Registry, unter macOS Homebrew und mpv.app - jeweils Orte,
    die oft nicht im PATH stehen."""
    exe = "mpv.exe" if ipc.WINDOWS else "mpv"
    if configured:
        path = os.path.expandvars(os.path.expanduser(configured))
        if os.path.isdir(path):
            path = os.path.join(path, exe)
        return path if os.path.isfile(path) else None

    candidates = []
    if ipc.WINDOWS:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates.append(os.path.join(here, exe))
        # Entpackt heißt der Ordner z.B. mpv-x86_64-20250928-git-..., je nach
        # Entpackprogramm noch mit einem Unterordner gleichen Namens
        for pattern in (("mpv*", exe), ("mpv*", "*", exe)):
            candidates += sorted(glob.glob(os.path.join(here, *pattern)), reverse=True)
    candidates.append(shutil.which(exe))
    if ipc.WINDOWS:
        candidates += _registered_mpv()
    elif sys.platform == "darwin":
        candidates += [
            "/opt/homebrew/bin/mpv",
            "/usr/local/bin/mpv",
            "/Applications/mpv.app/Contents/MacOS/mpv",
            os.path.expanduser("~/Applications/mpv.app/Contents/MacOS/mpv"),
        ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def _registered_mpv() -> list[str]:
    """Windows: wo Installer mpv in der Registry eintragen, ohne es in den
    PATH zu legen - App Paths (mpv-install.bat aus dem mpv-Download) und
    "Öffnen mit" (u.a. winget install shinchiro.mpv)."""
    import winreg

    keys = [
        r"Software\Microsoft\Windows\CurrentVersion\App Paths\mpv.exe",
        r"Software\Classes\Applications\mpv.exe\shell\open\command",
    ]
    paths = []
    for key in keys:
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                value = winreg.QueryValue(root, key)
            except OSError:
                continue
            # z.B. "C:\Program Files\MPV Player\mpv.exe" "%1"
            match = re.match(r'\s*"?([^"]*?mpv\.exe)', os.path.expandvars(value), re.IGNORECASE)
            if match:
                paths.append(match.group(1))
    return paths


def _find_mpris_plugin() -> Optional[str]:
    """Sucht nach dem mpv-mpris-Plugin (macht mpv über D-Bus/MPRIS steuerbar,
    z.B. per Medientasten oder dem Media-Widget von GNOME/KDE - inklusive
    Vorspulen, auch wenn das Terminal nicht fokussiert ist).

    Fragt zuerst dpkg direkt (zuverlässig, unabhängig vom genauen Pfad, den
    die jeweilige Distro verwendet), fällt sonst auf bekannte Pfade zurück.
    Nur Linux - unter Windows/macOS bringt mpv seine Mediensteuerung selbst mit.
    """
    if not sys.platform.startswith("linux"):
        return None
    try:
        result = subprocess.run(
            ["dpkg", "-L", "mpv-mpris"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.endswith(".so") and os.path.isfile(line):
                    return line
    except (OSError, subprocess.SubprocessError):
        pass

    candidates = [
        os.path.expanduser("~/.config/mpv/scripts/mpris.so"),
        "/etc/mpv/scripts/mpris.so",
        *glob.glob("/usr/lib/*/mpv-mpris/mpris.so"),
        *glob.glob("/usr/lib/mpv-mpris/mpris.so"),
        *glob.glob("/usr/lib/*/mpris.so"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


# Spart RAM/Threads: mpvs eingebaute Lua-Skripte (Bildschirm-Bedienelemente,
# Konsole, Statistik, youtube-dl, ...) werden ohne Fenster nie gebraucht, und
# der Cache hält nur ein Stück des Titels statt der ganzen Datei (~45 s
# CD-FLAC voraus, 10 s Zurückspulen bleibt im Cache). Bricht die Verbindung
# ab (z.B. nach langer Pause), setzt mpv selbst an der richtigen Stelle fort.
LEAN_OPTIONS = [
    "--osc=no",
    "--ytdl=no",
    "--load-stats-overlay=no",
    "--load-console=no",
    "--load-auto-profiles=no",
    "--load-select=no",
    "--load-positioning=no",
    "--load-commands=no",
    "--demuxer-max-bytes=8MiB",
    "--demuxer-max-back-bytes=4MiB",
    # Den nächsten Titel schon öffnen, während der laufende zu Ende geht:
    # kein Stocken beim Wechsel, gerade über VPN/Tunnel
    "--prefetch-playlist=yes",
]

# mpv liest auch die mpv.conf des Benutzers. Diese Einstellungen daraus
# würden den Player aushebeln (bleibt am Titelende stehen, Titel starten
# mittendrin, ...) - auf der Kommandozeile gewinnen sie gegen die mpv.conf.
OVERRIDE_OPTIONS = [
    "--keep-open=no",
    "--loop-file=no",
    "--loop-playlist=no",
    "--shuffle=no",
    "--save-position-on-quit=no",
    "--resume-playback=no",
]

REPLAYGAIN_MODES = ("track", "album", "no")

# Welche Optionen die installierte mpv-Version kennt, pro mpv-Datei gemerkt
OPTIONS_CACHE = CACHE_DIR / "mpv-options.json"


def _list_options(mpv: str) -> Optional[set]:
    try:
        out = subprocess.run(
            [mpv, "--list-options"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            # großzügig: beim allerersten Start prüft unter Windows oft noch
            # der Virenscanner die mpv.exe
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return {line.split()[0] for line in out.splitlines() if line.lstrip().startswith("--")}


def _supported(mpv: str, options: list[str]) -> list[str]:
    """Filtert Optionen heraus, die die installierte mpv-Version nicht kennt -
    mpv würde sonst gar nicht erst starten (z.B. ältere mpv unter Ubuntu).

    Das Ergebnis wird gemerkt, bis sich die mpv-Datei ändert: mpv
    --list-options bei jedem Start wäre ein zusätzlicher Prozess (unter
    Windows mit Virenscanner auch mal Sekunden)."""
    names = [opt.split("=")[0] for opt in options]
    try:
        path = os.path.abspath(shutil.which(mpv) or mpv)
        st = os.stat(path)
        stamp = [path, st.st_mtime_ns, st.st_size]
    except OSError:
        stamp = None
    known: dict = {}
    try:
        data = json.loads(OPTIONS_CACHE.read_text(encoding="utf-8"))
        if stamp and data.get("mpv") == stamp and isinstance(data.get("known"), dict):
            known = data["known"]
    except (OSError, ValueError, AttributeError):
        pass
    if any(name not in known for name in names):
        available = _list_options(mpv)
        if available is None:
            return []
        known = {name: name in available for name in names}
        if stamp:
            try:
                OPTIONS_CACHE.parent.mkdir(parents=True, exist_ok=True)
                OPTIONS_CACHE.write_text(json.dumps({"mpv": stamp, "known": known}), encoding="utf-8")
            except OSError:
                pass
    return [opt for opt, name in zip(options, names) if known.get(name)]


def _kill_with_us(proc: subprocess.Popen) -> Optional[int]:
    """Windows: steckt mpv in ein Job-Objekt, das beim Ende unseres Prozesses
    alles darin beendet. Wird das Konsolenfenster mit X geschlossen, kommt
    Python nicht mehr zum Aufräumen - und mpv (ohne eigene Konsole) liefe
    sonst unsichtbar weiter. Gibt den Handle zurück (muss offen bleiben)."""
    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in ("read_ops", "write_ops", "other_ops", "read", "write", "other")
        ]

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if kernel32.SetInformationJobObject(
            job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
        ) and kernel32.AssignProcessToJobObject(job, int(proc._handle)):
            return job
        kernel32.CloseHandle(job)
    except (OSError, AttributeError, ValueError):
        pass
    return None


class MPVPlayer:
    EXECUTABLE = "mpv"  # run.py setzt hier den Pfad aus find_mpv() ein
    LOG_DIR = str(STATE_DIR)
    LOG_PATH = os.path.join(LOG_DIR, "mpv.log")
    LOG_MAX_BYTES = 1_000_000  # darüber wird beim Start neu angefangen (alte -> .1)
    # Was die Oberfläche jede Sekunde braucht - in einer einzigen Rundreise geholt
    STATUS = ("time-pos", "duration", "pause", "volume", "idle-active", "playlist-pos")

    def __init__(self, replaygain: str = "track", extra_args: Optional[list[str]] = None):
        # Über den Socket lässt sich mpv komplett steuern (bis hin zum Starten
        # von Programmen) - daher im privaten Laufzeitordner statt in /tmp.
        # Kurzer Name: macOS erlaubt nur 104 Zeichen, und dessen $TMPDIR ist lang.
        name = f"jfclite-mpv-{uuid.uuid4().hex[:16]}"
        if ipc.WINDOWS:
            self._socket_path = rf"\\.\pipe\{name}"
        else:
            self._socket_path = os.path.join(
                os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(),
                f"{name}.sock",
            )
        if replaygain not in REPLAYGAIN_MODES:
            replaygain = "no"
        args = [
            self.EXECUTABLE,
            "--no-video",
            "--idle=yes",
            "--force-window=no",
            "--msg-level=all=warn,mpris=info",
            f"--input-ipc-server={self._socket_path}",
            *_supported(
                self.EXECUTABLE,
                [*LEAN_OPTIONS, *OVERRIDE_OPTIONS, f"--replaygain={replaygain}"],
            ),
        ]
        if extra_args:
            args.extend(extra_args)
        # mpv-mpris automatisch laden, falls installiert, aber noch nicht
        # in einem der von mpv automatisch gescannten Script-Ordner liegt.
        # Das gibt System-Mediensteuerung (Medientasten, GNOME/KDE-Widget).
        # realpath: Debian legt /etc/mpv/scripts/mpris.so als Symlink auf
        # /usr/lib/mpv-mpris/mpris.so an - ohne Auflösen würde das Plugin
        # doppelt geladen (zwei MPRIS-Player, kaputte D-Bus-Registrierung).
        auto_scanned = {
            os.path.realpath(os.path.expanduser("~/.config/mpv/scripts/mpris.so")),
            os.path.realpath("/etc/mpv/scripts/mpris.so"),
        }
        plugin = _find_mpris_plugin()
        if plugin and os.path.realpath(plugin) not in auto_scanned:
            args.append(f"--script={plugin}")

        os.makedirs(self.LOG_DIR, exist_ok=True)
        try:
            if os.path.getsize(self.LOG_PATH) > self.LOG_MAX_BYTES:
                os.replace(self.LOG_PATH, self.LOG_PATH + ".1")
        except OSError:
            pass
        with open(self.LOG_PATH, "a", encoding="utf-8") as log:
            log.write(
                f"\n--- Start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n"
                f"MPRIS-Plugin gefunden: {plugin or 'NEIN - mpv-mpris installiert?'}\n"
                f"mpv-Kommando: {' '.join(args)}\n"
            )
        log_file = open(self.LOG_PATH, "a", encoding="utf-8")
        self._log_file = log_file
        # stdin=DEVNULL: sonst liest mpv Tastendrücke aus demselben Terminal
        # mit und schnappt sie der TUI weg.
        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            # Windows: kein eigenes Konsolenfenster für mpv aufpoppen lassen
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._job = _kill_with_us(self._proc) if ipc.WINDOWS else None
        self._sock = None  # socket.socket oder ipc._WindowsPipe
        self._recv_buf = b""
        self._request_id = 0
        self._current_pos = 0  # Playlist-Index des laufenden Titels, siehe set_neighbors()
        # Fehlgeschlagene Ladeversuche in Folge (Server weg, Token ungültig,
        # defekte Datei) - ein erfolgreich geöffneter Titel setzt auf 0 zurück
        self.failures = 0
        # Großzügiges Zeitlimit wegen des Virenscanners beim ersten Start
        # unter Windows - beendet sich mpv vorher, geht es sofort weiter.
        self._connect(timeout=15)

    def _connect(self, timeout: float = 5) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline and self._proc.poll() is None:
            # (Windows-Pipes nicht per exists() prüfen - das verbraucht eine
            # Verbindung von mpv)
            if ipc.WINDOWS or os.path.exists(self._socket_path):
                try:
                    self._sock = ipc.connect(self._socket_path, timeout=2)
                    return
                except OSError:
                    pass
            time.sleep(0.05)
        raise RuntimeError(f"Keine Verbindung zu mpv, Details stehen in {self.LOG_PATH}")

    def _read_line(self) -> Optional[dict]:
        """Liest genau eine JSON-Zeile vom Socket (aus Puffer oder neu empfangen)."""
        while b"\n" not in self._recv_buf:
            try:
                chunk = self._sock.recv(4096)
            except OSError:  # Zeitlimit, oder mpv ist weg (abgestürzt/beendet)
                return None
            if not chunk:
                return None
            self._recv_buf += chunk
        line, _, self._recv_buf = self._recv_buf.partition(b"\n")
        try:
            return json.loads(line.decode("utf-8"))
        except Exception:
            return None

    def _handle_event(self, msg: dict) -> None:
        event = msg.get("event")
        if event == "end-file" and msg.get("reason") == "error":
            self.failures += 1
        elif event == "file-loaded":
            self.failures = 0

    def _send(self, *commands: Any) -> list[int]:
        """Schickt alle Kommandos auf einmal - eine Rundreise statt einer pro
        Kommando (unter Windows wartet jede einzelne auf die Pipe)."""
        if self._sock is None:
            return []
        ids, lines = [], []
        for command in commands:
            self._request_id += 1
            ids.append(self._request_id)
            lines.append(json.dumps({"command": command, "request_id": self._request_id}) + "\n")
        try:
            self._sock.sendall("".join(lines).encode("utf-8"))
        except OSError:  # mpv ist weg - die TUI soll dabei nicht mit abstürzen
            return []
        return ids

    def _replies(self, ids: list[int]) -> dict:
        """Wartet gezielt auf DIE Antworten mit passender request_id - mpv
        schickt über denselben Socket auch unaufgeforderte Events (z.B. beim
        Trackwechsel), die hier ausgewertet und übersprungen werden."""
        pending, replies = set(ids), {}
        for _ in range(50 + len(ids)):  # Obergrenze gegen Endlosschleife bei Event-Flut
            if not pending:
                break
            msg = self._read_line()
            if msg is None:
                break
            if "event" in msg:
                self._handle_event(msg)
            elif msg.get("request_id") in pending:
                pending.discard(msg["request_id"])
                replies[msg["request_id"]] = msg
        return replies

    def _command(self, *args: Any) -> Any:
        return self._run(list(args))

    def _run(self, command: Any) -> Any:
        ids = self._send(command)
        return self._replies(ids).get(ids[0]) if ids else None

    def load(self, url: str, start: Optional[float] = None) -> None:
        """Spielt url ab, mit start ab dieser Sekunde."""
        if start:
            # Benannte Argumente: ab mpv 0.38 steht vor den Optionen noch ein Index
            command: Any = {"name": "loadfile", "url": url, "flags": "replace",
                            "options": f"start={start:.1f}"}
        else:
            command = ["loadfile", url, "replace"]
        self._current_pos = 0  # "replace" leert die Playlist
        if not self.get_property("idle-active"):
            self._run(command)
            return
        # mpv-mpris 0.7 (Debian) meldet den Wechsel Leerlauf -> Wiedergabe
        # nicht und bleibt auf "Stopped" - KDE/GNOME halten den Player dann
        # für gestoppt. Ein echter Pause-Wechsel setzt den Status korrekt.
        self.play_pause(True)
        self._run(command)
        self.play_pause(False)

    def set_http_headers(self, headers: list[str]) -> None:
        """Per IPC statt als Startargument: Argumente sieht jeder Benutzer
        des PCs (z.B. mit ps)."""
        self._command("set_property", "http-header-fields", headers)

    def set_replaygain(self, mode: str) -> None:
        if mode in REPLAYGAIN_MODES:
            self._command("set_property", "replaygain", mode)

    def set_neighbors(self, prev_url: Optional[str], next_url: Optional[str]) -> None:
        """Legt Vorgänger/Nachfolger um den laufenden Titel in mpvs Playlist:
        so funktionieren Weiter/Zurück über MPRIS (Medientasten, KDE/GNOME-
        Widget), und mpv spielt am Titelende direkt weiter."""
        commands: list = [["playlist-clear"]]  # entfernt alles außer dem laufenden Titel
        if next_url:
            commands.append(["loadfile", next_url, "append"])
        if prev_url:
            commands.append(["loadfile", prev_url, "append"])
            commands.append(["playlist-move", 2 if next_url else 1, 0])
        self._replies(self._send(*commands))
        self._current_pos = 1 if prev_url else 0

    def playlist_step(self, pos: Optional[int] = None) -> int:
        """+1/-1, wenn mpv seit set_neighbors() selbst zum Nachfolger/Vorgänger
        gewechselt ist (Titelende oder MPRIS), sonst 0. pos: playlist-pos,
        falls schon bekannt."""
        if pos is None:
            pos = self.get_property("playlist-pos")
        if pos is None or pos < 0:
            return 0
        step = pos - self._current_pos
        return step if step in (-1, 1) else 0

    def play_pause(self, pause: Optional[bool] = None) -> None:
        if pause is None:
            self._command("cycle", "pause")
        else:
            self._command("set_property", "pause", pause)

    def change_volume(self, delta: int) -> None:
        # "add" statt eigenem Zähler: bleibt synchron, wenn die Lautstärke
        # über MPRIS (KDE/GNOME-Widget) geändert wurde; mpv begrenzt selbst.
        self._command("add", "volume", delta)

    def set_volume(self, percent: int) -> None:
        self._command("set_property", "volume", max(0, percent))

    def get_property(self, name: str) -> Any:
        resp = self._command("get_property", name)
        if resp and "data" in resp:
            return resp["data"]
        return None

    def get_properties(self, names: Iterable[str]) -> dict:
        """Mehrere Eigenschaften in einer Rundreise; fehlende sind None."""
        names = list(names)
        result = dict.fromkeys(names)
        ids = self._send(*(["get_property", name] for name in names))
        replies = self._replies(ids)
        for name, req_id in zip(names, ids):
            result[name] = replies.get(req_id, {}).get("data")
        return result

    def status(self) -> dict:
        return self.get_properties(self.STATUS)

    @property
    def alive(self) -> bool:
        return self._proc.poll() is None

    def seek(self, seconds: float, relative: bool = True) -> None:
        self._command("seek", seconds, "relative" if relative else "absolute")

    def shutdown(self) -> None:
        try:
            self._command("quit")
        except Exception:
            pass
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=1)
        except Exception:
            pass
        if not ipc.WINDOWS:
            try:
                os.remove(self._socket_path)
            except OSError:
                pass
        try:
            self._log_file.close()
        except Exception:
            pass
