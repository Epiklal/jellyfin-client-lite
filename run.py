#!/usr/bin/env python3
"""Startet jellyfin-client-lite: liest config.json und startet die TUI."""

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Optional

from jfclite import CACHE_DIR, CONFIG_DIR, STATE_DIR
from jfclite.player import MPVPlayer, find_mpv

OLD_NAME = "jellyfin-tui-lite"  # Name bis 0.1.x
WINDOWS = sys.platform == "win32"
HERE = Path(__file__).resolve().parent
XDG_CONFIG_DIR = CONFIG_DIR
XDG_CONFIG_PATH = XDG_CONFIG_DIR / "config.json"
LOCAL_CONFIG_PATH = HERE / "config.json"  # für Dev-/Quellordner-Betrieb
EXAMPLE_PATH = HERE / "config.example.json"


def find_config_path() -> Path:
    """Bevorzugt ~/.config/jellyfin-client-lite/config.json (für die .deb-Installation),
    fällt sonst auf eine config.json neben run.py zurück (Quellordner-Betrieb)."""
    candidates = (XDG_CONFIG_PATH, LOCAL_CONFIG_PATH)
    # Windows blendet bekannte Endungen aus: eine in config.json umbenannte
    # "Neue Textdatei" heißt in Wahrheit config.json.txt
    candidates += tuple(p.with_name(p.name + ".txt") for p in candidates)
    for path in candidates:
        if path.exists():
            return path
    return XDG_CONFIG_PATH  # bevorzugter Ort, falls noch keine existiert


def migrate_old_dirs() -> None:
    """Übernimmt Einstellungen, Titelliste und Logs aus der Zeit, als das
    Projekt noch jellyfin-tui-lite hieß (einmalig, nur wenn es den neuen
    Ordner noch nicht gibt)."""
    for new in (CONFIG_DIR, CACHE_DIR, STATE_DIR):
        old = new.with_name(OLD_NAME)
        if old.is_dir() and not new.exists():
            shutil.move(str(old), str(new))


def load_config() -> Optional[dict]:
    """Die Konfiguration, oder None, wenn es noch keine gibt (oder sie leer ist)."""
    path = find_config_path()
    if not path.exists():
        return None
    # Als Bytes: json erkennt dann selbst UTF-8 mit BOM und UTF-16, wie sie
    # Windows-Editoren und PowerShell gern schreiben.
    data = path.read_bytes()
    if not data.strip():
        return None
    try:
        return json.loads(data)
    except UnicodeDecodeError:
        print(f"{path} ist nicht als UTF-8 gespeichert. Im Editor beim")
        print("Speichern die Codierung UTF-8 wählen.")
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"Fehler in {path}, Zeile {exc.lineno}, Spalte {exc.colno}: {exc.msg}")
        if "escape" in exc.msg:
            print(r'In Pfaden \ verdoppeln oder / nehmen: "C:\\mpv\\mpv.exe" oder "C:/mpv/mpv.exe"')
            sys.exit(1)
        print("Häufigste Ursache: am Ende der Zeile DAVOR fehlt ein Komma.")
        print("Jede Zeile außer der letzten vor } muss mit , enden, z.B.:")
        print('  "password": "...",')
        print('  "discord_client_id": "123456789012345678"')
        sys.exit(1)


def config_hint() -> str:
    path = find_config_path()
    XDG_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if WINDOWS or path.exists():
        if path.exists():
            head = f"{path} ist leer (vielleicht nicht gespeichert?)."
        else:
            head = (
                "Keine Konfiguration gefunden. Zum Anlegen das hier eingeben, Notepad\n"
                "fragt dann, ob es die Datei erstellen soll (Ja):\n"
                f'  notepad "{path}"'
            )
        return (
            f"{head}\n"
            "Hinein kommt, mit deinen Daten:\n"
            "  {\n"
            '    "server_url": "http://192.168.1.10:8096",\n'
            '    "username": "dein-name",\n'
            '    "password": "dein-passwort"\n'
            "  }"
        )
    lines = [f"Keine Konfiguration gefunden. Bitte {path} anlegen, z.B.:"]
    lines.append(f"  mkdir -p {XDG_CONFIG_DIR}")
    if EXAMPLE_PATH.exists():
        lines.append(f"  cp {EXAMPLE_PATH} {path}")
    else:
        lines.append(f'  echo \'{{"server_url": "http://IP:8096", "username": "...", "password": "..."}}\' > {path}')
    lines.append(f"  nano {path}   # Serveradresse, Benutzername, Passwort eintragen")
    return "\n".join(lines)


def mpv_hint(configured: Optional[str]) -> str:
    if configured:
        return f'"mpv_path" in der Konfiguration zeigt auf {configured},\naber dort liegt kein mpv.'
    if WINDOWS:
        return (
            "mpv fehlt. Am einfachsten im Terminal:\n"
            "  winget install shinchiro.mpv\n"
            "Oder von https://mpv.io/installation/ die Windows-Version von shinchiro\n"
            f"holen (mpv-x86_64-...7z) und entpacken nach {HERE}\n"
            "Beides findet der Player von selbst. Liegt mpv woanders, in der\n"
            'config.json "mpv_path": "C:/Pfad/zu/mpv.exe" eintragen.'
        )
    if sys.platform == "darwin":
        return "mpv fehlt. Installieren mit:\n  brew install mpv"
    return "mpv fehlt. Installieren, z.B.:\n  sudo apt install mpv   (Fedora: dnf, Arch: pacman)"


def packages_hint() -> Optional[str]:
    """Hinweis statt Traceback, wenn Textual/requests fehlen - z.B. weil mit
    dem System-Python statt dem aus .venv gestartet."""
    missing = [m for m in ("textual", "requests") if importlib.util.find_spec(m) is None]
    if not missing:
        return None
    if WINDOWS:
        return (
            f"Es fehlen Python-Pakete ({', '.join(missing)}). Starte den Player mit\n"
            "jellyfin-client-lite.cmd (Doppelklick), das richtet sie beim ersten Mal ein."
        )
    lines = [f"Es fehlen Python-Pakete ({', '.join(missing)}). Einmalig in {HERE}:"]
    if not (HERE / ".venv").is_dir():
        lines.append("  python3 -m venv .venv")
    lines.append("  .venv/bin/python -m pip install -r requirements.txt")
    lines.append("Danach so starten:\n  .venv/bin/python run.py")
    return "\n".join(lines)


def main() -> None:
    migrate_old_dirs()
    config = load_config()
    mpv_path = (config or {}).get("mpv_path")
    mpv = find_mpv(mpv_path)
    # Alles auf einmal melden statt Stück für Stück
    problems = [
        hint
        for hint in (
            None if config is not None else config_hint(),
            None if mpv else mpv_hint(mpv_path),
            packages_hint(),
        )
        if hint
    ]
    if problems:
        print("\n\n".join(problems))
        sys.exit(1)
    MPVPlayer.EXECUTABLE = mpv

    import requests

    from jfclite.client import JellyfinClient
    from jfclite.app import JellyfinClientLite

    client = JellyfinClient(server_url=config["server_url"])
    try:
        client.login(config["username"], config["password"])
    except (requests.ConnectionError, requests.Timeout):
        print(f"Jellyfin unter {config['server_url']} ist nicht erreichbar.")
        print("Stimmt die Adresse in der Konfiguration, und läuft der Server?")
        sys.exit(1)
    except Exception as exc:
        print(f"Anmeldung bei Jellyfin fehlgeschlagen: {exc}")
        sys.exit(1)

    presence = None
    if config.get("discord_client_id"):
        from jfclite.discord import DiscordPresence

        presence = DiscordPresence(
            config["discord_client_id"], cover_base_url=config.get("public_url")
        )

    app = JellyfinClientLite(client, presence=presence)
    try:
        app.run()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        client.logout()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\nBeendet.")
        sys.exit(0)
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
