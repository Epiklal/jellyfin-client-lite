"""Erster Start: Server und Anmeldung abfragen und die Config schreiben.

Läuft im normalen Terminal (vor der Oberfläche), damit es überall ohne
Zusatzpakete funktioniert - auch unter Windows.
"""

from __future__ import annotations

import getpass
import json
import os
import time
from pathlib import Path
from typing import Optional

import requests

from .client import JellyfinClient

QUICK_CONNECT_TIMEOUT = 300  # Sekunden, so lange gilt ein Code auf dem Server


def save_config(path: Path, config: dict) -> None:
    """Schreibt die Config atomar und nur für den Besitzer lesbar (Passwort bzw.
    Token stehen darin)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    return input(f"{prompt}{suffix}: ").strip() or default


def _normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url and "://" not in url:
        url = "http://" + url
    return url


def _ask_server(default: str) -> JellyfinClient:
    while True:
        url = _normalize_url(_ask("Serveradresse (z.B. 192.168.1.10:8096)", default))
        if not url:
            continue
        client = JellyfinClient(url)
        try:
            info = client.server_info()
        except requests.RequestException as exc:
            print(f"  Keine Verbindung zu {url}: {type(exc).__name__}")
            print("  Adresse und Port prüfen (Jellyfin nutzt meist :8096).\n")
            continue
        print(f"  Verbunden mit „{info.get('ServerName', '?')}“ (Jellyfin {info.get('Version', '?')})\n")
        return client


def _password_login(client: JellyfinClient, config: dict) -> bool:
    username = _ask("Benutzername", config.get("username", ""))
    password = getpass.getpass("Passwort (bleibt beim Tippen unsichtbar): ")
    try:
        client.login(username, password)
    except RuntimeError as exc:
        print(f"  {exc}\n")
        return False
    except requests.RequestException as exc:
        print(f"  Anmeldung nicht möglich: {exc}\n")
        return False
    config.update(username=username, password=password)
    config.pop("access_token", None)
    return True


def _quick_connect_login(client: JellyfinClient, config: dict) -> bool:
    try:
        code, secret = client.quick_connect_start()
    except (RuntimeError, requests.RequestException) as exc:
        print(f"  {exc}\n")
        return False
    print(f"\n  Dein Code:  {code}\n")
    print("  In Jellyfin (Browser oder App) anmelden, dann")
    print("  Benutzermenü → Quick Connect → Code eingeben. Abbruch mit Strg+C.")
    deadline = time.monotonic() + QUICK_CONNECT_TIMEOUT
    try:
        while time.monotonic() < deadline:
            time.sleep(3)
            if client.quick_connect_authorized(secret):
                client.quick_connect_login(secret)
                break
        else:
            print("  Der Code ist abgelaufen.\n")
            return False
    except KeyboardInterrupt:
        print("\n  Abgebrochen.\n")
        return False
    except requests.RequestException as exc:
        print(f"  Quick Connect fehlgeschlagen: {exc}\n")
        return False
    config.update(access_token=client.api_key)
    config.pop("username", None)
    config.pop("password", None)
    return True


def run_setup(existing: Optional[dict] = None) -> dict:
    """Fragt Serveradresse und Anmeldeart ab, prüft alles gegen den Server und
    gibt die fertige Config zurück (bestehende Einstellungen bleiben erhalten)."""
    config = dict(existing or {})
    print("Einrichtung von jellyfin-client-lite\n")
    client = _ask_server(config.get("server_url", ""))
    config["server_url"] = client.server_url
    while True:
        print("Wie willst du dich anmelden?")
        print("  1  Benutzername und Passwort")
        print("  2  Quick Connect (Code in einem schon angemeldeten Jellyfin bestätigen)")
        choice = _ask("Auswahl", "1")
        if choice == "2":
            ok = _quick_connect_login(client, config)
        elif choice == "1":
            ok = _password_login(client, config)
        else:
            continue
        if ok:
            break
    # Jellyfin-Sitzung von der Prüfung wieder schließen; beim Start meldet sich
    # run.py selbst an (bei Quick Connect bleibt der Token gültig)
    client.logout()
    print("Angemeldet. Die Einstellungen (Autoplay usw.) öffnest du im Player mit [s].\n")
    return config
