"""Erster Start: Server und Anmeldung abfragen und die Config schreiben.

Läuft im normalen Terminal (vor der Oberfläche), damit es überall ohne
Zusatzpakete funktioniert - auch unter Windows.
"""

from __future__ import annotations

import getpass
import ipaddress
import json
import os
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import requests

from .client import JellyfinClient
from .i18n import t

QUICK_CONNECT_TIMEOUT = 300  # Sekunden, so lange gilt ein Code auf dem Server
INTERNAL_DOMAINS = (".local", ".lan", ".home", ".internal", ".home.arpa", ".fritz.box", ".ts.net")


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


def _is_exposed_http(url: str) -> bool:
    """Unverschlüsselt und nach außen? Interne Adressen (privates Netz, Tailscale,
    Namen ohne Domain) zählen nicht - das lässt sich nur schätzen, ein Hinweis
    zu viel schadet aber nicht."""
    parts = urlsplit(url)
    if parts.scheme != "http":
        return False
    host = (parts.hostname or "").lower()
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    return "." in host and not host.endswith(INTERNAL_DOMAINS)


def _ask_server(default: str) -> JellyfinClient:
    while True:
        url = _normalize_url(_ask(t("Serveradresse (z.B. 192.168.1.10:8096)"), default))
        if not url:
            continue
        client = JellyfinClient(url)
        try:
            info = client.server_info()
        except requests.RequestException as exc:
            print(t("  Keine Verbindung zu {url}: {error}", url=url, error=type(exc).__name__))
            print(t("  Adresse und Port prüfen (Jellyfin nutzt meist :8096).\n"))
            continue
        print(
            t(
                "  Verbunden mit „{name}“ (Jellyfin {version})\n",
                name=info.get("ServerName", "?"),
                version=info.get("Version", "?"),
            )
        )
        if _is_exposed_http(url):
            print(t("  Achtung: http:// ist unverschlüsselt, Passwort und Token wären mitlesbar."))
            print(t("  Über das Internet besser https:// verwenden.\n"))
        return client


def _password_login(client: JellyfinClient, config: dict) -> bool:
    username = _ask(t("Benutzername"), config.get("username", ""))
    password = getpass.getpass(t("Passwort (bleibt beim Tippen unsichtbar): "))
    try:
        client.login(username, password)
    except RuntimeError as exc:
        print(f"  {exc}\n")
        return False
    except requests.RequestException as exc:
        print(t("  Anmeldung nicht möglich: {error}\n", error=exc))
        return False
    # Nur der Token kommt in die Config, nicht das Passwort. Der Benutzername
    # bleibt als Vorschlag für die nächste Einrichtung.
    client.keep_token = True
    config.update(username=username, access_token=client.api_key)
    config.pop("password", None)
    return True


def _quick_connect_login(client: JellyfinClient, config: dict) -> bool:
    try:
        code, secret = client.quick_connect_start()
    except (RuntimeError, requests.RequestException) as exc:
        print(f"  {exc}")
        print(t("  Alternative: Anmeldung mit Passwort (Auswahl 2).\n"))
        return False
    print(t("\n  Dein Code:  {code}\n", code=code))
    print(t("  In Jellyfin (Browser oder App) anmelden, dann"))
    print(t("  Benutzermenü → Quick Connect → Code eingeben. Abbruch mit Strg+C."))
    deadline = time.monotonic() + QUICK_CONNECT_TIMEOUT
    try:
        while time.monotonic() < deadline:
            time.sleep(3)
            if client.quick_connect_authorized(secret):
                client.quick_connect_login(secret)
                break
        else:
            print(t("  Der Code ist abgelaufen.\n"))
            return False
    except KeyboardInterrupt:
        print(t("\n  Abgebrochen.\n"))
        return False
    except requests.RequestException as exc:
        print(t("  Quick Connect fehlgeschlagen: {error}\n", error=exc))
        return False
    config.update(access_token=client.api_key)
    config.pop("username", None)
    config.pop("password", None)
    return True


def _ask_discord(config: dict) -> None:
    """Optional: Application-ID für die Anzeige in Discord ("Hört ...")."""
    current = config.get("discord_client_id", "")
    print(t("Optional: in Discord (auch Equibop, Vesktop) anzeigen, was läuft."))
    print(t("Dafür unter https://discord.com/developers/applications eine Anwendung"))
    print(t("anlegen (ihr Name wird angezeigt) und hier ihre Application-ID eingeben."))
    hint = t("Enter = behalten, - = entfernen") + f") [{current}]" if current else t("Enter = keine") + ")"
    while True:
        answer = input(f"Discord-Application-ID ({hint}: ").strip()
        if answer == "-":
            config.pop("discord_client_id", None)
            break
        if not answer:
            break  # bisherige behalten (oder weiter keine)
        if answer.isdigit() and 17 <= len(answer) <= 20:
            config["discord_client_id"] = answer
            break
        print(t("  Die ID besteht nur aus Ziffern (17-20 Stellen)."))
    print()


def run_setup(existing: Optional[dict] = None) -> dict:
    """Fragt Serveradresse und Anmeldeart ab, prüft alles gegen den Server und
    gibt die fertige Config zurück (bestehende Einstellungen bleiben erhalten)."""
    config = dict(existing or {})
    print(t("Einrichtung von jellyfin-client-lite\n"))
    client = _ask_server(config.get("server_url", ""))
    config["server_url"] = client.server_url
    while True:
        print(t("Wie willst du dich anmelden?"))
        print(
            t(
                "  1  Quick Connect (empfohlen: Code in einem schon angemeldeten Jellyfin bestätigen,\n"
                "     es wird kein Passwort gespeichert)"
            )
        )
        print(t("  2  Benutzername und Passwort (gespeichert wird auch hier nur ein Token)"))
        choice = _ask(t("Auswahl"), "1")
        if choice == "1":
            ok = _quick_connect_login(client, config)
        elif choice == "2":
            ok = _password_login(client, config)
        else:
            continue
        if ok:
            break
    print(t("Angemeldet.\n"))
    _ask_discord(config)
    print(t("Fertig. Die Einstellungen (Autoplay usw.) öffnest du im Player mit [s].\n"))
    return config
