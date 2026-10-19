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

DEVICE_NAME = NAME
CLIENT_NAME = NAME
CLIENT_VERSION = __version__

# Titel-Liste vom letzten Start: der Server braucht ~3 ms pro Titel, um die
# Bibliothek auszuliefern (3000 Titel = ~10 s) - aus dem Cache startet die
# Wiedergabe sofort, neu geladen wird nur, wenn sich etwas geändert hat.
CACHE_PATH = CACHE_DIR / "library.json"
CACHE_VERSION = 3  # erhöhen, wenn sich die Felder von Track ändern

# Reihenfolge und Position beim Beenden, für start_mode "resume"
QUEUE_PATH = CACHE_DIR / "queue.json"

# Steuerzeichen in Tags (z.B. ESC) gingen sonst ungefiltert ans Terminal: eine
# präparierte Datei könnte Fenstertitel oder Zwischenablage (OSC 52)
# manipulieren. Dazu Bidi-Steuerzeichen, die Text optisch umdrehen.
_UNSAFE_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f‪-‮⁦-⁩]+")


def _clean(text: str) -> str:
    return _UNSAFE_CHARS.sub(" ", text).strip()


def new_device_id() -> str:
    """Einmal pro Installation erzeugt und in der Config gespeichert. Meldet
    sich dasselbe Gerät neu an (--setup), ersetzt Jellyfin den alten Token,
    statt einen weiteren anzulegen."""
    return f"{NAME}-{uuid.uuid4().hex[:12]}"


class AuthError(RuntimeError):
    """Anmeldung abgelehnt (falsches Passwort, Token abgelaufen/widerrufen)."""


@dataclass
class Track:
    id: str
    title: str
    artist: str
    album: str
    duration_ticks: int  # Jellyfin ticks (1 tick = 100ns)
    album_id: str = ""  # für das Cover (hängt meist am Album, nicht am Titel)
    album_artist: str = ""  # zum Sortieren: hält Sampler zusammen
    disc: int = 0
    number: int = 0

    def __post_init__(self) -> None:
        self.title = _clean(self.title)
        self.artist = _clean(self.artist)
        self.album = _clean(self.album)
        self.album_artist = _clean(self.album_artist)

    @property
    def duration_seconds(self) -> float:
        return self.duration_ticks / 10_000_000

    @property
    def duration_str(self) -> str:
        secs = int(self.duration_seconds)
        return f"{secs // 60}:{secs % 60:02d}"


def _track_from_item(item: dict) -> Track:
    # Die Interpreten des Titels, nicht der Album-Interpret: bei Samplern
    # stünde sonst überall "Various Artists".
    artists = ", ".join(a for a in item.get("Artists") or [] if a)
    artist = artists or item.get("AlbumArtist") or "Unbekannt"
    return Track(
        id=item["Id"],
        title=item.get("Name") or "Unbekannt",
        artist=artist,
        album=item.get("Album") or "",
        duration_ticks=item.get("RunTimeTicks") or 0,
        album_id=item.get("AlbumId") or "",
        album_artist=item.get("AlbumArtist") or artist,
        disc=item.get("ParentIndexNumber") or 0,
        number=item.get("IndexNumber") or 0,
    )


def _write_json(path: Path, data: dict) -> None:
    """Atomar schreiben; Fehler sind egal (Cache bzw. Komfortfunktion)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


class JellyfinClient:
    def __init__(self, server_url: str, device_id: str):
        self.server_url = server_url.rstrip("/")
        self.api_key: Optional[str] = None  # Access Token, nach der Anmeldung
        self.user_id: Optional[str] = None
        # Jellyfin 10.12 nimmt die alten X-Emby-*-Header standardmäßig nicht
        # mehr an, nur noch "Authorization: MediaBrowser ...".
        self._auth = (
            f'MediaBrowser Client="{CLIENT_NAME}", Device="{DEVICE_NAME}", '
            f'DeviceId="{device_id}", Version="{CLIENT_VERSION}"'
        )
        self._session = requests.Session()
        self._use_token(None)

    def _use_token(self, token: Optional[str]) -> None:
        header = self._auth + (f', Token="{token}"' if token else "")
        self._session.headers["Authorization"] = header

    def login(self, username: str, password: str) -> None:
        """Meldet sich mit Benutzername + Passwort an und holt sich damit
        einen Access Token, der danach wie ein API-Key benutzt wird."""
        resp = self._session.post(
            f"{self.server_url}/Users/AuthenticateByName",
            json={"Username": username, "Pw": password},
            timeout=10,
        )
        if resp.status_code == 401:
            raise AuthError("Anmeldung fehlgeschlagen: Benutzername oder Passwort falsch.")
        resp.raise_for_status()
        self._set_token(resp.json())

    def _set_token(self, data: dict) -> None:
        self.api_key = data["AccessToken"]
        self.user_id = data["User"]["Id"]
        self._use_token(self.api_key)

    def server_info(self) -> dict:
        """Name und Version des Servers (ohne Anmeldung) - zugleich ein Test,
        ob die Adresse stimmt."""
        resp = self._session.get(f"{self.server_url}/System/Info/Public", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def login_with_token(self, token: str) -> None:
        """Übernimmt den gespeicherten Token und prüft ihn."""
        self._use_token(token)
        resp = self._session.get(f"{self.server_url}/Users/Me", timeout=10)
        if resp.status_code == 401:
            self._use_token(None)
            raise AuthError("Die gespeicherte Anmeldung ist abgelaufen oder wurde widerrufen.")
        resp.raise_for_status()
        self.api_key = token
        self.user_id = resp.json()["Id"]

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

    def resolve_user_id(self) -> str:
        if not self.user_id:
            raise RuntimeError("Nicht angemeldet - login() wurde nicht aufgerufen.")
        return self.user_id

    def _items(self, params: dict, timeout) -> dict:
        resp = self._session.get(
            f"{self.server_url}/Items",
            params={
                "userId": self.resolve_user_id(),
                "IncludeItemTypes": "Audio",
                "Recursive": "true",
                # Bild-Infos und Wiedergabe-Status (UserData) werden nicht
                # gebraucht - spart dem Server Datenbankabfragen und
                # verkleinert die Antwort (schnellerer Start über VPN).
                "EnableImages": "false",
                "EnableUserData": "false",
                **params,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def fetch_all_tracks(self) -> List[Track]:
        """Holt ALLE Audio-Titel der Bibliothek in Seiten (schonend für den Server).

        (connect, read)-Timeout: das erste Laden einer großen Bibliothek oder
        ein Zugriff über einen Tunnel/VPN (z.B. Tailscale) kann eine Weile
        dauern, daher hier bewusst großzügig statt schnell abzubrechen."""
        # 10 min Puffer gegen leicht verstellte Uhren zwischen PC und Server
        fetched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 600))
        tracks: List[Track] = []
        seen = set()
        start = 0
        page_size = 500
        while True:
            data = self._items(
                {
                    # Feste Reihenfolge, sonst ist nicht garantiert, dass die
                    # Seiten lückenlos aneinander passen
                    "SortBy": "SortName,DateCreated",
                    "StartIndex": start,
                    "Limit": page_size,
                },
                timeout=(15, 180),
            )
            items = data.get("Items", [])
            for item in items:
                # Läuft gerade ein Scan, verschieben sich die Seiten: ein Titel
                # kann doppelt kommen (und die Tabelle verträgt keine doppelten IDs)
                if item["Id"] not in seen:
                    seen.add(item["Id"])
                    tracks.append(_track_from_item(item))
            start += page_size
            if start >= data.get("TotalRecordCount", 0) or not items:
                break
        self._save_cache(tracks, fetched_at)
        return tracks

    def load_cache(self) -> Optional[Tuple[List[Track], str]]:
        """Titel-Liste vom letzten Laden (gleicher Server/Benutzer) und wann sie
        geladen wurde, oder None."""
        data = self._load_for_me(CACHE_PATH)
        if data is None or data.get("version") != CACHE_VERSION:
            return None
        try:
            return [Track(*t) for t in data["tracks"]], data["fetched_at"]
        except (KeyError, TypeError):
            return None

    def _load_for_me(self, path: Path) -> Optional[dict]:
        """JSON-Datei, sofern sie zu diesem Server und Benutzer gehört."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data["server_url"] != self.server_url or data["user_id"] != self.user_id:
                return None
            return data
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _save_cache(self, tracks: List[Track], fetched_at: str) -> None:
        _write_json(
            CACHE_PATH,
            {
                "version": CACHE_VERSION,
                "server_url": self.server_url,
                "user_id": self.user_id,
                "fetched_at": fetched_at,
                "tracks": [astuple(t) for t in tracks],
            },
        )

    def save_queue(self, ids: List[str], index: int, position: float, shuffle: bool) -> None:
        _write_json(
            QUEUE_PATH,
            {
                "server_url": self.server_url,
                "user_id": self.user_id,
                "ids": ids,
                "index": index,
                "position": round(position, 1),
                "shuffle": shuffle,
            },
        )

    def load_queue(self) -> Optional[dict]:
        """Gespeicherte Reihenfolge: {"ids", "index", "position", "shuffle"}."""
        data = self._load_for_me(QUEUE_PATH)
        if data is None or not isinstance(data.get("ids"), list):
            return None
        return data

    def library_changed(self, cached_count: int, since: str) -> bool:
        """Zwei billige Zähl-Abfragen (je ~20-40 ms) statt die ganze Bibliothek
        neu zu laden: andere Anzahl = Titel gelöscht/hinzugefügt, Titel mit
        neuerem Speicherdatum = hinzugefügt oder Metadaten geändert."""
        return (
            self._count_tracks() != cached_count
            or self._count_tracks(MinDateLastSaved=since) > 0
        )

    def _count_tracks(self, **filters: str) -> int:
        data = self._items({"Limit": 0, **filters}, timeout=(15, 60))
        return data.get("TotalRecordCount", 0)

    def fetch_cover(self, item_id: str) -> Optional[bytes]:
        """Kleines Cover (Album oder Titel), None wenn es keins gibt. Der Token
        geht als Header mit, nicht in der Adresse."""
        resp = self._session.get(
            f"{self.server_url}/Items/{item_id}/Images/Primary",
            params={"maxWidth": 96, "maxHeight": 96, "quality": 80},
            timeout=10,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.content

    def fetch_lyrics(self, track_id: str) -> List[Tuple[Optional[float], str]]:
        """Songtext, den der Server zum Titel hinterlegt hat (Zeilen mit Startzeit
        in Sekunden, falls synchronisiert). Leer, wenn es keinen gibt."""
        resp = self._session.get(f"{self.server_url}/Audio/{track_id}/Lyrics", timeout=10)
        if resp.status_code in (404, 400):
            return []
        resp.raise_for_status()
        lines = []
        for line in resp.json().get("Lyrics", [])[:1000]:
            start = line.get("Start")
            lines.append(
                (
                    start / 10_000_000 if start is not None else None,
                    _clean(line.get("Text") or ""),
                )
            )
        return lines

    def stream_url(self, track_id: str) -> str:
        """Direkter, nicht transcodierter Stream = minimale Serverlast/CPU.

        Bewusst OHNE Token in der Adresse: mpv veröffentlicht sie über MPRIS
        (xesam:url) und zeigt sie als Titel, wenn eine Datei keine Tags hat.
        Der Token geht stattdessen als HTTP-Header mit, s. stream_headers()."""
        return f"{self.server_url}/Audio/{track_id}/stream?static=true"

    def stream_headers(self) -> List[str]:
        return [f'Authorization: MediaBrowser Token="{self.api_key}"']


def shuffled(tracks: List[Track]) -> List[Track]:
    copy = list(tracks)
    random.shuffle(copy)
    return copy


def sorted_tracks(tracks: List[Track]) -> List[Track]:
    return sorted(
        tracks,
        key=lambda t: (
            t.album_artist.casefold(),
            t.album.casefold(),
            t.disc,
            t.number,
            t.title.casefold(),
        ),
    )
