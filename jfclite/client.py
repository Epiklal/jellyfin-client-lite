"""Sehr schlanker Jellyfin-Client: nur das Nötigste, um alle Audio-Titel
zu laden und Stream-URLs zu bauen. Keine unnötigen Requests, kein Polling."""

from __future__ import annotations

import json
import os
import random
import re
import time
import uuid
from dataclasses import astuple, dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import requests

from . import CACHE_DIR, NAME, __version__

# Pro Prozessstart eine eigene, zufällige Geräte-ID: verhindert, dass ein
# zweiter (versehentlich parallel laufender) Start dem ersten die Jellyfin-
# Sitzung/den Token wegnimmt (Jellyfin invalidiert sonst bei gleicher
# Geräte-ID die vorherige Sitzung - das führte zu sporadischen 401ern).
DEVICE_ID = f"{NAME}-{uuid.uuid4().hex[:12]}"
DEVICE_NAME = NAME
CLIENT_NAME = NAME
CLIENT_VERSION = __version__

# Titel-Liste vom letzten Start: der Server braucht ~3 ms pro Titel, um die
# Bibliothek auszuliefern (3000 Titel = ~10 s) - aus dem Cache startet die
# Wiedergabe sofort, neu geladen wird nur, wenn sich etwas geändert hat.
CACHE_PATH = CACHE_DIR / "library.json"
CACHE_VERSION = 2  # erhöhen, wenn sich die Felder von Track ändern

# Steuerzeichen in Tags (z.B. ESC) gingen sonst ungefiltert ans Terminal: eine
# präparierte Datei könnte Fenstertitel oder Zwischenablage (OSC 52)
# manipulieren. Dazu Bidi-Steuerzeichen, die Text optisch umdrehen.
_UNSAFE_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]+")


@dataclass
class Track:
    id: str
    title: str
    artist: str
    album: str
    duration_ticks: int  # Jellyfin ticks (1 tick = 100ns)
    album_id: str = ""  # für das Cover (hängt meist am Album, nicht am Titel)

    def __post_init__(self) -> None:
        self.title = _UNSAFE_CHARS.sub(" ", self.title).strip()
        self.artist = _UNSAFE_CHARS.sub(" ", self.artist).strip()
        self.album = _UNSAFE_CHARS.sub(" ", self.album).strip()

    @property
    def duration_seconds(self) -> float:
        return self.duration_ticks / 10_000_000

    @property
    def duration_str(self) -> str:
        secs = int(self.duration_seconds)
        return f"{secs // 60}:{secs % 60:02d}"


class JellyfinClient:
    def __init__(self, server_url: str):
        self.server_url = server_url.rstrip("/")
        self.api_key: Optional[str] = None  # wird nach login() gesetzt (Access Token)
        self.user_id: Optional[str] = None
        # Quick-Connect-Token liegen in der Config und müssen das Programmende
        # überleben - nur Token aus Benutzername + Passwort werden abgemeldet.
        self.keep_token = False
        self._session = requests.Session()
        self._session.headers.update(
            {
                "X-Emby-Authorization": (
                    f'MediaBrowser Client="{CLIENT_NAME}", Device="{DEVICE_NAME}", '
                    f'DeviceId="{DEVICE_ID}", Version="{CLIENT_VERSION}"'
                ),
            }
        )

    def login(self, username: str, password: str) -> None:
        """Meldet sich mit Benutzername + Passwort an und holt sich damit
        einen Access Token, der danach wie ein API-Key benutzt wird."""
        resp = self._session.post(
            f"{self.server_url}/Users/AuthenticateByName",
            json={"Username": username, "Pw": password},
            timeout=10,
        )
        if resp.status_code == 401:
            raise RuntimeError("Anmeldung fehlgeschlagen: Benutzername oder Passwort falsch.")
        resp.raise_for_status()
        self._set_token(resp.json())

    def _set_token(self, data: dict) -> None:
        self.api_key = data["AccessToken"]
        self.user_id = data["User"]["Id"]
        self._session.headers.update({"X-Emby-Token": self.api_key})

    def server_info(self) -> dict:
        """Name und Version des Servers (ohne Anmeldung) - zugleich ein Test,
        ob die Adresse stimmt."""
        resp = self._session.get(f"{self.server_url}/System/Info/Public", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def login_with_token(self, token: str) -> None:
        """Übernimmt einen gespeicherten Token (Quick Connect) und prüft ihn."""
        self._session.headers.update({"X-Emby-Token": token})
        resp = self._session.get(f"{self.server_url}/Users/Me", timeout=10)
        if resp.status_code == 401:
            self._session.headers.pop("X-Emby-Token", None)
            raise RuntimeError(
                "Die gespeicherte Anmeldung ist abgelaufen oder wurde widerrufen."
            )
        resp.raise_for_status()
        self.api_key = token
        self.user_id = resp.json()["Id"]
        self.keep_token = True

    def quick_connect_start(self) -> Tuple[str, str]:
        """Startet Quick Connect: (Code zum Eingeben in Jellyfin, Geheimnis)."""
        resp = self._session.post(f"{self.server_url}/QuickConnect/Initiate", timeout=10)
        if resp.status_code in (400, 401, 403):
            raise RuntimeError("Quick Connect ist auf diesem Server nicht aktiviert.")
        resp.raise_for_status()
        data = resp.json()
        return data["Code"], data["Secret"]

    def quick_connect_authorized(self, secret: str) -> bool:
        resp = self._session.get(
            f"{self.server_url}/QuickConnect/Connect",
            params={"secret": secret},
            timeout=10,
        )
        resp.raise_for_status()
        return bool(resp.json().get("Authenticated"))

    def quick_connect_login(self, secret: str) -> None:
        resp = self._session.post(
            f"{self.server_url}/Users/AuthenticateWithQuickConnect",
            json={"Secret": secret},
            timeout=10,
        )
        resp.raise_for_status()
        self._set_token(resp.json())
        self.keep_token = True

    def resolve_user_id(self) -> str:
        if not self.user_id:
            raise RuntimeError("Nicht angemeldet - login() wurde nicht aufgerufen.")
        return self.user_id

    def fetch_all_tracks(self) -> List[Track]:
        """Holt ALLE Audio-Titel der Bibliothek in Seiten (schonend für den Server).

        (connect, read)-Timeout: das erste Laden einer großen Bibliothek oder
        ein Zugriff über einen Tunnel/VPN (z.B. Tailscale) kann eine Weile
        dauern, daher hier bewusst großzügig statt schnell abzubrechen."""
        # 10 min Puffer gegen leicht verstellte Uhren zwischen PC und Server
        fetched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 600))
        user_id = self.resolve_user_id()
        tracks: List[Track] = []
        start = 0
        page_size = 500
        while True:
            resp = self._session.get(
                f"{self.server_url}/Users/{user_id}/Items",
                params={
                    "IncludeItemTypes": "Audio",
                    "Recursive": "true",
                    "Fields": "RunTimeTicks",
                    # Bild-Infos und Wiedergabe-Status (UserData) werden nicht
                    # gebraucht - spart dem Server Datenbankabfragen und
                    # verkleinert die Antwort (schnellerer Start über VPN).
                    "EnableImages": "false",
                    "EnableUserData": "false",
                    "StartIndex": start,
                    "Limit": page_size,
                },
                timeout=(15, 180),
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("Items", [])
            for item in items:
                tracks.append(
                    Track(
                        id=item["Id"],
                        title=item.get("Name", "Unbekannt"),
                        artist=item.get("AlbumArtist") or item.get("Artists", ["Unbekannt"])[0]
                        if item.get("Artists")
                        else "Unbekannt",
                        album=item.get("Album", ""),
                        duration_ticks=item.get("RunTimeTicks", 0) or 0,
                        album_id=item.get("AlbumId", ""),
                    )
                )
            start += page_size
            if start >= data.get("TotalRecordCount", 0) or not items:
                break
        self._save_cache(tracks, fetched_at)
        return tracks

    def load_cache(self) -> Optional[Tuple[List[Track], str]]:
        """Titel-Liste vom letzten Laden (gleicher Server/Benutzer) und wann sie
        geladen wurde, oder None."""
        try:
            data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if (
                data.get("version") != CACHE_VERSION
                or data["server_url"] != self.server_url
                or data["user_id"] != self.user_id
            ):
                return None
            return [Track(*t) for t in data["tracks"]], data["fetched_at"]
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _save_cache(self, tracks: List[Track], fetched_at: str) -> None:
        data = {
            "version": CACHE_VERSION,
            "server_url": self.server_url,
            "user_id": self.user_id,
            "fetched_at": fetched_at,
            "tracks": [astuple(t) for t in tracks],
        }
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = CACHE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, CACHE_PATH)
        except OSError:
            pass  # ohne Cache geht es auch, dann eben beim nächsten Start langsamer

    def library_changed(self, cached_count: int, since: str) -> bool:
        """Zwei billige Zähl-Abfragen (je ~20-40 ms) statt die ganze Bibliothek
        neu zu laden: andere Anzahl = Titel gelöscht/hinzugefügt, Titel mit
        neuerem Speicherdatum = hinzugefügt oder Metadaten geändert."""
        return (
            self._count_tracks() != cached_count
            or self._count_tracks(MinDateLastSaved=since) > 0
        )

    def _count_tracks(self, **filters: str) -> int:
        resp = self._session.get(
            f"{self.server_url}/Users/{self.resolve_user_id()}/Items",
            params={
                "IncludeItemTypes": "Audio",
                "Recursive": "true",
                "Limit": 0,
                "EnableImages": "false",
                "EnableUserData": "false",
                **filters,
            },
            timeout=(15, 60),
        )
        resp.raise_for_status()
        return resp.json().get("TotalRecordCount", 0)

    def stream_url(self, track_id: str) -> str:
        """Direkter, nicht transcodierter Stream = minimale Serverlast/CPU.

        Bewusst OHNE Token in der Adresse: mpv veröffentlicht sie über MPRIS
        (xesam:url) und zeigt sie als Titel, wenn eine Datei keine Tags hat.
        Der Token geht stattdessen als HTTP-Header mit, s. stream_headers()."""
        return f"{self.server_url}/Audio/{track_id}/stream?static=true"

    def stream_headers(self) -> List[str]:
        return [f"X-Emby-Token: {self.api_key}"]

    def logout(self) -> None:
        """Meldet den Token beim Server ab - sonst bliebe nach jedem Start ein
        weiterer, unbegrenzt gültiger Token auf dem Server zurück."""
        if not self.api_key or self.keep_token:
            return
        try:
            self._session.post(f"{self.server_url}/Sessions/Logout", timeout=3)
        except requests.RequestException:
            pass
        self.api_key = None


def shuffled(tracks: List[Track]) -> List[Track]:
    copy = list(tracks)
    random.shuffle(copy)
    return copy


def sorted_tracks(tracks: List[Track]) -> List[Track]:
    return sorted(tracks, key=lambda t: (t.artist.casefold(), t.album.casefold(), t.title.casefold()))
