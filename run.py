#!/usr/bin/env python3
"""Startet jellyfin-client-lite: liest config.json und startet die TUI."""

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from jfclite import CACHE_DIR, CONFIG_DIR, NAME, STATE_DIR
from jfclite.i18n import set_language, t
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
        config = json.loads(data)
    except UnicodeDecodeError:
        print(t("{path} ist nicht als UTF-8 gespeichert. Im Editor beim", path=path))
        print(t("Speichern die Codierung UTF-8 wählen."))
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(t("Fehler in {path}, Zeile {line}, Spalte {column}: {error}", path=path, line=exc.lineno, column=exc.colno, error=exc.msg))
        if "escape" in exc.msg:
            print(t(r'In Pfaden \ verdoppeln oder / nehmen: "C:\\mpv\\mpv.exe" oder "C:/mpv/mpv.exe"'))
            sys.exit(1)
        print(t("Häufigste Ursache: am Ende der Zeile DAVOR fehlt ein Komma."))
        print(t("Jede Zeile außer der letzten vor } muss mit , enden, z.B.:"))
        print('  "password": "...",')
        print('  "discord_client_id": "123456789012345678"')
        sys.exit(1)
    if not isinstance(config, dict):
        print(
            t(
                "{path} enthält kein JSON-Objekt. Datei korrigieren oder löschen, beim\n"
                "nächsten Start läuft dann die Einrichtung.",
                path=path,
            )
        )
        sys.exit(1)
    return config


def missing_settings(config: dict) -> List[str]:
    """Was in der Config fehlt, um sich anmelden zu können (das Passwort darf
    leer sein, Jellyfin erlaubt Benutzer ohne)."""
    missing = [] if isinstance(config.get("server_url"), str) and config["server_url"] else ["server_url"]
    if not config.get("access_token"):
        if not config.get("username") or not isinstance(config["username"], str):
            missing.append("username")
        if not isinstance(config.get("password"), str):
            missing.append("password")
    return missing


def config_hint() -> str:
    path = find_config_path()
    XDG_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    intro = t(
        "Am einfachsten einmal im Terminal starten: die Einrichtung meldet dich per\n"
        "Quick Connect an, ganz ohne Passwort in der Datei. Von Hand geht es auch:"
    )
    if WINDOWS or path.exists():
        if path.exists():
            head = t("{path} ist leer (vielleicht nicht gespeichert?).", path=path)
        else:
            head = (
                t(
                    "Keine Konfiguration gefunden. Zum Anlegen das hier eingeben, Notepad\n"
                    "fragt dann, ob es die Datei erstellen soll (Ja):\n"
                )
                + f'  notepad "{path}"'
            )
        return (
            f"{intro}\n\n{head}\n"
            + t("Hinein kommt, mit deinen Daten:\n")
            + "  {\n"
            '    "server_url": "http://192.168.1.10:8096",\n'
            '    "username": "your-name",\n'
            '    "password": "your-password"\n'
            "  }"
        )
    lines = [intro, "", t("Keine Konfiguration gefunden. Bitte {path} anlegen, z.B.:", path=path)]
    lines.append(f"  mkdir -p {XDG_CONFIG_DIR}")
    if EXAMPLE_PATH.exists():
        lines.append(f"  cp {EXAMPLE_PATH} {path}")
    else:
        lines.append(f'  echo \'{{"server_url": "http://IP:8096", "username": "...", "password": "..."}}\' > {path}')
    lines.append(f"  nano {path}   # " + t("Serveradresse, Benutzername, Passwort eintragen"))
    return "\n".join(lines)


def mpv_hint(configured: Optional[str]) -> str:
    if configured:
        return t('"mpv_path" in der Konfiguration zeigt auf {path},\naber dort liegt kein mpv.', path=configured)
    if WINDOWS:
        return (
            t("mpv fehlt. Am einfachsten im Terminal:\n")
            + "  winget install shinchiro.mpv\n"
            + t(
                "Oder von https://mpv.io/installation/ die Windows-Version von shinchiro\n"
                "holen (mpv-x86_64-...7z) und entpacken nach {path}\n"
                "Beides findet der Player von selbst. Liegt mpv woanders, in der\n"
                'config.json "mpv_path": "C:/Pfad/zu/mpv.exe" eintragen.',
                path=HERE,
            )
        )
    if sys.platform == "darwin":
        return t("mpv fehlt. Installieren mit:\n") + "  brew install mpv"
    return t("mpv fehlt. Installieren, z.B.:\n") + "  sudo apt install mpv   (Fedora: dnf, Arch: pacman)"


def packages_hint() -> Optional[str]:
    """Hinweis statt Traceback, wenn Textual/requests fehlen - z.B. weil mit
    dem System-Python statt dem aus .venv gestartet."""
    missing = [m for m in ("textual", "requests") if importlib.util.find_spec(m) is None]
    if not missing:
        return None
    if WINDOWS:
        return t(
            "Es fehlen Python-Pakete ({packages}). Starte den Player mit\n"
            "jellyfin-client-lite.cmd (Doppelklick), das richtet sie beim ersten Mal ein.",
            packages=", ".join(missing),
        )
    lines = [t("Es fehlen Python-Pakete ({packages}). Einmalig in {path}:", packages=", ".join(missing), path=HERE)]
    if not (HERE / ".venv").is_dir():
        lines.append("  python3 -m venv .venv")
    lines.append("  .venv/bin/python -m pip install -r requirements.txt")
    lines.append(t("Danach so starten:\n") + "  .venv/bin/python run.py")
    return "\n".join(lines)


def setup_command() -> str:
    """Wie man die Einrichtung neu startet - je nachdem, wie installiert."""
    if WINDOWS:
        return f"{NAME}.cmd --setup"
    if HERE == Path("/usr/lib") / NAME:  # .deb
        return f"{NAME} --setup"
    python = ".venv/bin/python" if (HERE / ".venv").is_dir() else "python3"
    return f"{python} run.py --setup   (in {HERE})"


def main() -> None:
    migrate_old_dirs()
    # Vor dem Import von jfclite.app: dort werden die Tastenkürzel schon übersetzt
    # (Meldungen bis hierher gehen nach der Systemsprache)
    config = load_config()
    set_language((config or {}).get("language"))
    force_setup = "--setup" in sys.argv
    mpv_path = (config or {}).get("mpv_path")
    mpv = find_mpv(mpv_path)
    # Alles auf einmal melden statt Stück für Stück
    problems = [
        hint
        for hint in (
            None if config is not None or sys.stdin.isatty() else config_hint(),
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
    from jfclite.setup import run_setup, save_config

    config_path = find_config_path()
    if config is None or force_setup:
        # Erster Start (oder --setup): Server und Anmeldung abfragen
        config = run_setup(config)
        save_config(config_path, config)
        print(t("Gespeichert in {path}\n", path=config_path))

    missing = missing_settings(config)
    if missing:
        print(t("In {path} fehlt: {fields}", path=config_path, fields=", ".join(missing)))
        print(t("Neu einrichten mit:  {command}", command=setup_command()))
        sys.exit(1)

    client = JellyfinClient(server_url=config["server_url"])
    try:
        if config.get("access_token"):
            client.login_with_token(config["access_token"])
        else:
            client.login(config["username"], config["password"])
    except (requests.ConnectionError, requests.Timeout):
        print(t("Jellyfin unter {url} ist nicht erreichbar.", url=config["server_url"]))
        print(t("Stimmt die Adresse in der Konfiguration, und läuft der Server?"))
        sys.exit(1)
    except Exception as exc:
        print(t("Anmeldung bei Jellyfin fehlgeschlagen: {error}", error=exc))
        print(t("Neu einrichten mit:  {command}", command=setup_command()))
        sys.exit(1)

    presence = None
    if config.get("discord_client_id"):
        from jfclite.discord import DiscordPresence

        presence = DiscordPresence(
            config["discord_client_id"], cover_base_url=config.get("public_url")
        )

    from jfclite.reporting import PlaybackReporter

    app = JellyfinClientLite(
        client,
        presence=presence,
        config=config,
        save_config=lambda: save_config(config_path, config),
        reporter=PlaybackReporter(client),
    )
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
        print(t("\nBeendet."))
        sys.exit(0)
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
