from __future__ import annotations

import asyncio
import bisect
import os
import random
import signal
import threading
import time
from typing import Callable, List, Optional, Tuple

from rich.text import Text
from textual.actions import SkipAction
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Select,
    Static,
    Switch,
)

from . import NAME, ipc
from .client import JellyfinClient, Track, shuffled, sorted_tracks
from .discord import DiscordPresence
from .player import MPVPlayer
from .widgets import (
    Control,
    CoverArt,
    SeekBar,
    TrackTable,
    VolumeBar,
    cover_supported,
    render_cover,
    typical_width,
)

ACCENT = "#e63946"  # rot

CSS = f"""
Screen {{
    background: transparent;
}}

#main {{
    height: 1fr;
    background: transparent;
}}

#list-panel {{
    width: 1fr;
}}

#search {{
    display: none;
    border: round {ACCENT};
    background: transparent;
}}

#search.visible {{
    display: block;
}}

#tracks, #hits {{
    height: 1fr;
    overflow-x: hidden;
    border: round {ACCENT};
    border-title-color: {ACCENT};
    background: transparent;
}}

/* Suchtreffer in einer eigenen Tabelle: die große bleibt beim Suchen
   unangetastet und ist beim Schließen sofort wieder da */
#hits {{
    display: none;
}}

#list-panel.searching #hits {{
    display: block;
}}

#list-panel.searching #tracks {{
    display: none;
}}

#side {{
    width: 25%;
    min-width: 26;
    max-width: 40;
}}

#side.with-lyrics {{
    width: 35%;
    min-width: 32;
    max-width: 56;
}}

#lyrics-panel {{
    display: none;
    height: 2fr;
    border: round {ACCENT};
    border-title-color: {ACCENT};
    background: transparent;
    padding: 0 1;
}}

#side.with-lyrics #lyrics-panel {{
    display: block;
}}

#queue-panel {{
    height: 1fr;
    border: round {ACCENT};
    border-title-color: {ACCENT};
    background: transparent;
}}

#queue-panel > ListView {{
    background: transparent;
}}

ListItem {{
    background: transparent;
}}

#queue-list Label {{
    width: 100%;
    text-wrap: nowrap;
    text-overflow: ellipsis;
}}

DataTable {{
    background: transparent;
}}

DataTable > .datatable--cursor {{
    background: {ACCENT} 30%;
}}

#np-info {{
    width: 1fr;
}}

CoverArt {{
    display: none;
}}

#now-playing.with-cover {{
    height: 8;
}}

#now-playing.with-cover CoverArt {{
    display: block;
}}

#now-playing.with-cover #np-info {{
    align-vertical: middle;
}}

#now-playing {{
    height: 6;
    border: round {ACCENT};
    padding: 0 1;
    background: transparent;
}}

#np-title, #np-sub {{
    height: 1;
    text-wrap: nowrap;
    text-overflow: ellipsis;
}}

#np-title {{
    color: {ACCENT};
    text-style: bold;
}}

#np-sub {{
    color: #a86a6f;
}}

#controls {{
    height: 1;
}}

#controls-spacer {{
    width: 1fr;
}}

Control {{
    color: {ACCENT};
    text-style: bold;
}}

Control:hover {{
    background: {ACCENT} 30%;
}}

SettingsScreen {{
    align: center middle;
}}

/* passt so gerade in ein 80x24-Terminal, sonst wird gescrollt */
#settings {{
    width: 60;
    height: auto;
    max-height: 100%;
    overflow-y: auto;
    border: round {ACCENT};
    padding: 0 2;
    background: $surface;
}}

#settings-title {{
    color: {ACCENT};
    text-style: bold;
    margin: 1 0;
}}

#settings Horizontal {{
    height: auto;
}}

#settings Horizontal Label {{
    width: 1fr;
    padding-top: 1;
}}

#settings-buttons {{
    align-horizontal: right;
    margin-top: 1;
}}

#settings-buttons Button {{
    margin-left: 1;
}}
"""

START_MODES = [
    ("Zufällig gemischt", "shuffle"),
    ("Sortiert (Interpret, Album)", "sorted"),
    ("Fortsetzen", "resume"),
]
REPLAYGAIN_MODES = [
    ("Pro Titel", "track"),
    ("Pro Album", "album"),
    ("Aus", "no"),
]

MAX_HITS = 500  # mehr Treffer machen den Tabellenaufbau nur langsam
MAX_FAILURES = 3  # so viele Ladefehler in Folge: anhalten statt weiterzuspringen
RETRY_SECONDS = 15


def _choice(options: list, value, default: str) -> str:
    return value if value in [v for _, v in options] else default


class SettingsScreen(ModalScreen[Optional[dict]]):
    """Einstellungen; Ergebnis ist das geänderte Dict oder None bei Abbruch."""

    BINDINGS = [("escape", "dismiss(None)", "Abbrechen")]

    def __init__(self, settings: dict) -> None:
        super().__init__()
        self._settings = settings

    def compose(self) -> ComposeResult:
        with Vertical(id="settings"):
            yield Label("Einstellungen", id="settings-title")
            with Horizontal():
                yield Label("Autoplay (beim Start sofort abspielen)")
                yield Switch(self._settings["autoplay"], id="set-autoplay")
            with Horizontal():
                yield Label("Cover neben dem Titel anzeigen")
                yield Switch(self._settings["show_cover"], id="set-cover")
            with Horizontal():
                yield Label("Songtext anzeigen")
                yield Switch(self._settings["show_lyrics"], id="set-lyrics")
            with Horizontal():
                yield Label("Beim Start: Reihenfolge")
                yield Select(
                    START_MODES,
                    value=_choice(START_MODES, self._settings["start_mode"], "shuffle"),
                    allow_blank=False,
                    id="set-start-mode",
                )
            with Horizontal():
                yield Label("Lautstärke angleichen")
                yield Select(
                    REPLAYGAIN_MODES,
                    value=_choice(REPLAYGAIN_MODES, self._settings["replaygain"], "track"),
                    allow_blank=False,
                    id="set-replaygain",
                )
            with Horizontal(id="settings-buttons"):
                yield Button("Abbrechen", id="cancel")
                yield Button("Speichern", id="save", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.dismiss(
                {
                    "autoplay": self.query_one("#set-autoplay", Switch).value,
                    "show_cover": self.query_one("#set-cover", Switch).value,
                    "show_lyrics": self.query_one("#set-lyrics", Switch).value,
                    "start_mode": self.query_one("#set-start-mode", Select).value,
                    "replaygain": self.query_one("#set-replaygain", Select).value,
                }
            )
        else:
            self.dismiss(None)


class JellyfinClientLite(App):
    CSS = CSS
    TITLE = NAME
    AUTO_FOCUS = "#tracks"
    BINDINGS = [
        ("space", "toggle_pause", "Play/Pause"),
        ("n", "next_track", "Nächster Titel"),
        ("p", "prev_track", "Vorheriger Titel"),
        ("left", "seek_back", "-10s"),
        ("right", "seek_fwd", "+10s"),
        ("r", "reshuffle", "Neu mischen"),
        # priority: sonst landet der "/" nach dem Fokuswechsel noch im Suchfeld
        Binding("slash", "search", "Suche", priority=True),
        ("l", "toggle_lyrics", "Songtext"),
        ("c", "toggle_cover", "Cover"),
        ("s", "settings", "Einstellungen"),
        Binding("g", "goto_current", "Zum laufenden Titel", show=False),
        Binding("escape", "close_search", "Suche schließen", show=False),
        ("plus", "vol_up", "Lauter"),
        Binding("=", "vol_up", "Lauter", show=False),  # US-Layout
        ("-", "vol_down", "Leiser"),
        ("q", "quit", "Beenden"),
    ]

    def __init__(
        self,
        client: JellyfinClient,
        presence: Optional[DiscordPresence] = None,
        config: Optional[dict] = None,
        save_config: Optional[Callable[[], None]] = None,
    ):
        # ansi_color=True: Textual malt keine eigene RGB-Hintergrundfarbe über
        # den Screen, sondern lässt die Standardfarbe (und damit z.B. die
        # Transparenz/Opacity-Einstellung) des Terminals durchscheinen.
        # Ältere Textual-Versionen (<0.80) kennen dieses Argument noch nicht -
        # dann eben ohne (dann bleibt der Hintergrund blickdicht).
        try:
            super().__init__(ansi_color=True)
        except TypeError:
            super().__init__()
        self.client = client
        self.presence = presence
        self.player: Optional[MPVPlayer] = None
        self.config = config if config is not None else {}
        self._save_config = save_config
        self.all_tracks: List[Track] = []
        self.queue: List[Track] = []
        self.current_index: int = -1
        self._ordered_shuffle = self.config.get("start_mode") != "sorted"
        self.hits: List[Track] = []  # was die Treffer-Tabelle zeigt
        self._hits_total = 0
        self.search_active = False  # Queue besteht nur aus Suchtreffern
        self._search_timer = None
        self._haystack: List[Tuple[Track, str]] = []  # (Titel, Suchtext)
        self._title_cells: dict = {}  # id() der Titel-Zelle -> Titel-ID, s. _set_queue()
        self._table_ids: set = set()  # Titel in der großen Tabelle
        self._follow_row = -1  # Zeile, zu der der Cursor zuletzt mitgewandert ist
        self._loading = False
        self._started = False
        self._finished = False
        self._stalled = False  # Titel lassen sich nicht laden, s. _while_stalled()
        self._retry_at = 0.0
        self._mpv_gone = False
        self._resume: Optional[Tuple[int, float]] = None  # (Queue-Index, Sekunde)
        self._pending_update = False  # Bibliothek geändert, während die Suche offen war
        self._pos_sample = (0.0, 0.0, True)  # (Position, Zeitpunkt, pausiert)
        self._extras_for = ""  # Titel, für den Cover/Songtext geladen (werden)
        self._covers: dict = {}  # Cover-ID -> fertig gerendertes Bild (None = keins)
        self._lyrics: list = []  # (Startzeit oder None, Text)
        self._lyrics_synced = False
        self._lyric_starts: List[float] = []
        self._lyric_line = -1
        self._lyric_rows: Optional[Tuple[int, List[int]]] = None  # (Breite, Startzeile je Zeile)

    @property
    def main(self):
        """Der Hauptbildschirm - auch dann, wenn gerade ein Dialog darüberliegt
        (die Wiedergabe läuft ja weiter und aktualisiert ihre Anzeige)."""
        return self.screen_stack[0]

    @property
    def tracks_table(self) -> TrackTable:
        return self.main.query_one("#tracks", TrackTable)

    @property
    def hits_table(self) -> TrackTable:
        return self.main.query_one("#hits", TrackTable)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            with Vertical(id="list-panel"):
                # disabled, solange es unsichtbar ist: sonst bekäme es den Start-
                # fokus bzw. käme per Tab dran
                search = Input(
                    placeholder="Suche in Titel, Interpret, Album ...",
                    id="search",
                    disabled=True,
                )
                search.border_title = "Suche"
                search.border_subtitle = "Enter = zur Liste, Esc = schließen"
                yield search
                hits = TrackTable(id="hits", cursor_type="row")
                hits.border_subtitle = "Klick oder Enter = abspielen"
                yield hits
                table = TrackTable(id="tracks", cursor_type="row")
                table.border_subtitle = "Klick oder Enter = abspielen, g = zum laufenden Titel"
                yield table
            with Vertical(id="side"):
                with Vertical(id="queue-panel"):
                    yield Label("Queue", id="queue-label")
                    yield ListView(id="queue-list")
                with VerticalScroll(id="lyrics-panel"):
                    yield Static("", id="lyrics-text", markup=False)
        with Horizontal(id="now-playing"):
            yield CoverArt(id="cover")
            with Vertical(id="np-info"):
                yield Static("Lade Bibliothek ...", id="np-title", markup=False)
                yield Static("", id="np-sub", markup=False)
                yield SeekBar(ACCENT, id="np-progress")
                with Horizontal(id="controls"):
                    yield Control("⏮", "prev_track")
                    yield Control("⏸", "toggle_pause", id="btn-play")
                    yield Control("⏭", "next_track")
                    yield Control("⇄ Mischen", "reshuffle")
                    yield Static(id="controls-spacer")
                    yield Control("−", "vol_down")
                    yield VolumeBar(ACCENT)
                    yield Control("+", "vol_up")
        yield Footer()

    def on_mount(self) -> None:
        for table in self.main.query(TrackTable):
            table.add_columns("Titel", "Interpret", "Album", "Dauer")
        self._update_titles()
        self.main.query_one("#lyrics-panel").border_title = "Songtext"
        if self.config.get("show_cover", True) and not cover_supported():
            self.notify("Für Cover fehlt Pillow:  pip install pillow", severity="warning")
        self._apply_view()
        self._set_status(
            "Lade Bibliothek ...",
            "Kann bei großen Bibliotheken oder über VPN/Tunnel einen Moment dauern.",
        )
        self._install_signal_handlers()
        self._start_loading()
        self.run_worker(self._start_player, thread=True, exit_on_error=False)

    def _set_status(self, title: str, sub: str = "") -> None:
        self.main.query_one("#np-title", Static).update(title)
        self.main.query_one("#np-sub", Static).update(sub)

    # --- Start und Ende ---------------------------------------------------

    def _install_signal_handlers(self) -> None:
        """Fenster geschlossen (SIGHUP) oder `kill`/Herunterfahren (SIGTERM):
        ohne Handler beendet sich Python sofort, ohne aufzuräumen - die
        Position wäre nicht gespeichert, und bei SIGTERM liefe mpv weiter."""
        if ipc.WINDOWS:
            return  # dort sorgt das Job-Objekt in player.py dafür, dass mpv mitstirbt
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGHUP, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._on_signal)
            except (NotImplementedError, RuntimeError, ValueError):
                pass

    def _on_signal(self) -> None:
        self._cleanup()  # sofort: das Terminal ist womöglich schon weg
        # Falls Textual beim Aufräumen am geschlossenen Terminal hängen bleibt
        watchdog = threading.Timer(3, os._exit, (0,))
        watchdog.daemon = True
        watchdog.start()
        self.exit()

    def _cleanup(self) -> None:
        """Position merken, Discord-Anzeige entfernen, mpv beenden. Mehrfacher
        Aufruf schadet nicht."""
        if self._finished:
            return
        self._finished = True
        try:
            self._save_queue(self._estimated_position())
        except Exception:
            pass
        if self.presence:
            self.presence.close()
        if self.player:
            self.player.shutdown()

    def on_unmount(self) -> None:
        self._cleanup()

    def _start_loading(self) -> None:
        if self._loading:
            return  # z.B. [r], während die Bibliothek noch lädt
        self._loading = True
        self.run_worker(self._load_library, thread=True, exit_on_error=False)

    def _load_library(self) -> None:
        cached = self.client.load_cache()
        if cached:
            # Sofort aus dem Cache loslegen, danach im Hintergrund prüfen, ob
            # sich auf dem Server etwas geändert hat.
            tracks, fetched_at = cached
            self.call_from_thread(self._on_library_loaded, tracks)
            try:
                if not self.client.library_changed(len(tracks), fetched_at):
                    return
                tracks = self.client.fetch_all_tracks()
            except Exception:
                return  # Server gerade nicht erreichbar -> mit dem Cache weiter
            self.call_from_thread(self._on_library_updated, tracks)
            return
        try:
            tracks = self.client.fetch_all_tracks()
        except Exception as exc:
            self.call_from_thread(self._on_library_error, exc)
            return
        self.call_from_thread(self._on_library_loaded, tracks)

    def _start_player(self) -> None:
        """Läuft parallel zum Laden der Bibliothek: mpv starten dauert (unter
        Windows mit Virenscanner auch mal Sekunden) und soll die Oberfläche
        nicht einfrieren."""
        try:
            player = MPVPlayer(replaygain=self.config.get("replaygain", "track"))
            player.set_http_headers(self.client.stream_headers())
        except Exception as exc:
            self.call_from_thread(self._on_player_error, exc)
            return
        try:
            self.call_from_thread(self._on_player_ready, player)
        except RuntimeError:  # Programm wurde inzwischen beendet
            player.shutdown()

    def _on_player_ready(self, player: MPVPlayer) -> None:
        if self._finished:
            player.shutdown()
            return
        self.player = player
        self._maybe_begin()

    def _on_player_error(self, exc: Exception) -> None:
        self._set_status("⚠ mpv konnte nicht gestartet werden", str(exc))

    def _on_library_error(self, exc: Exception) -> None:
        self._loading = False
        self._set_status(
            "⚠ Bibliothek konnte nicht geladen werden",
            f"{type(exc).__name__}: {exc}  —  [r] zum erneuten Versuch",
        )

    def _on_library_loaded(self, tracks: List[Track]) -> None:
        self._loading = False
        if not tracks:
            self._set_status("Keine Titel in der Jellyfin-Bibliothek gefunden.")
            return
        self._set_library(tracks)
        if not (self.config.get("start_mode") == "resume" and self._restore_queue()):
            self._reshuffle(tracks)
        self._maybe_begin()

    def _maybe_begin(self) -> None:
        """Losspielen, sobald Bibliothek und mpv beide bereit sind."""
        if self._started or self.player is None or not self.queue:
            return
        self._started = True
        if self.config.get("autoplay", True):
            self._play_track(self._resume[0] if self._resume else 0)
        else:
            what = "Fortsetzen" if self._resume else "Abspielen"
            self._set_status(f"{len(self.all_tracks)} Titel geladen - Leertaste oder Enter zum {what}")
            if self._resume:
                self._follow(self.tracks_table, self._resume[0])
        self.set_interval(1.0, self._tick)
        self.set_interval(0.25, self._sync_lyrics)

    def _set_library(self, tracks: List[Track]) -> None:
        self.all_tracks = tracks
        # Suchtext einmal vorberechnen statt bei jedem Tastendruck
        self._haystack = [(t, f"{t.title} {t.artist} {t.album}".casefold()) for t in tracks]

    def _on_library_updated(self, tracks: List[Track]) -> None:
        if not tracks:
            return
        self._set_library(tracks)
        if self._search_open():
            # Nicht unter den Fingern umbauen - beim Schließen der Suche nachholen
            self._pending_update = True
            return
        self._apply_library_update()

    def _apply_library_update(self) -> None:
        """Neue Titelliste übernehmen, ohne den laufenden Titel zu unterbrechen
        und ohne die Queue neu zu mischen: Gelöschtes fällt heraus, Neues wird
        in den noch nicht gespielten Teil gemischt."""
        self._pending_update = False
        index = self.current_index if self.current_index >= 0 else -1
        if index < 0 and self._resume:
            index = self._resume[0]
        current = self.queue[index] if 0 <= index < len(self.queue) else None
        queue, new_index = self._merge([t.id for t in self.queue], index, current)
        self._set_queue(queue)
        if self.current_index >= 0:
            self.current_index = new_index
            self._update_neighbors()
            self._show_current()
        elif self._resume:
            self._resume = (new_index, self._resume[1]) if new_index >= 0 else None
        self.notify(f"Bibliothek aktualisiert: {len(self.all_tracks)} Titel")

    # --- Reihenfolge ------------------------------------------------------

    def _arrange(self, tracks: List[Track]) -> List[Track]:
        return shuffled(tracks) if self._ordered_shuffle else sorted_tracks(tracks)

    def _reshuffle(
        self,
        tracks: List[Track],
        first: Optional[Track] = None,
        shuffle: Optional[bool] = None,
    ) -> None:
        """Ordnet neu (gemischt oder sortiert) und bringt die Tabelle in die
        neue Reihenfolge - sonst passt die markierte Zeile nicht mehr zum
        laufenden Titel."""
        if shuffle is not None:
            self._ordered_shuffle = shuffle
        queue = self._arrange(tracks)
        if first is not None:
            queue = [first] + [t for t in queue if t.id != first.id]
        self._resume = None
        self.current_index = -1
        self._set_queue(queue)

    def _merge(
        self, ids: List[str], index: int, current: Optional[Track] = None
    ) -> Tuple[List[Track], int]:
        """Bringt eine Reihenfolge (Titel-IDs) auf den Stand der Bibliothek und
        gibt sie mit dem neuen Index des Titels an `index` zurück. Gibt es den
        nicht mehr, bleibt `current` (der gerade laufende) an seinem Platz, sonst
        geht es mit dem nächsten weiter."""
        by_id = {t.id: t for t in self.all_tracks}
        cur_id = ids[index] if 0 <= index < len(ids) else None
        if not self._ordered_shuffle:
            queue = sorted_tracks(self.all_tracks)
            new_index = next((i for i, t in enumerate(queue) if t.id == cur_id), -1)
            if new_index < 0 and current is not None:
                queue.insert(0, current)
                new_index = 0
            if new_index < 0 and index >= 0:
                new_index = 0
            return queue, new_index
        played = [by_id[i] for i in ids[: max(index, 0)] if i in by_id]
        upcoming = [by_id[i] for i in ids[index + 1 :] if i in by_id]
        known = set(ids)
        for t in self.all_tracks:
            if t.id not in known:
                upcoming.insert(random.randint(0, len(upcoming)), t)
        if cur_id in by_id:
            middle = [by_id[cur_id]]
        elif current is not None:
            middle = [current]
        else:
            middle = []
        queue = played + middle + upcoming
        new_index = len(played) if index >= 0 else -1
        if new_index >= len(queue):
            new_index = 0 if queue else -1
        return queue, new_index

    def _set_queue(self, queue: List[Track]) -> None:
        """Neue Queue in die große Tabelle. Sind es dieselben Titel wie vorher,
        wird nur umsortiert: neu aufbauen vermisst jede Zelle neu, bei 3000
        Titeln steht die Oberfläche dabei rund eine Sekunde."""
        self.queue = queue
        table = self.tracks_table
        ids = [t.id for t in queue]
        if len(ids) == table.row_count and set(ids) == self._table_ids:
            # sort() reicht dem key nur die Zellen durch - die Titel-Zelle ist
            # pro Zeile ein eigenes Text-Objekt und verrät so den Titel
            rank = {track_id: i for i, track_id in enumerate(ids)}
            cells = self._title_cells
            table.sort(key=lambda row: rank[cells[id(row[0])]])
        else:
            self._title_cells = self._fill(table, queue)
            self._table_ids = set(ids)
        self._follow_row = -1
        self._update_titles()

    def _restore_queue(self) -> bool:
        """start_mode "resume": Reihenfolge und Position vom letzten Mal."""
        state = self.client.load_queue()
        if not state:
            return False
        ids, index = state["ids"], state.get("index")
        if not (isinstance(index, int) and 0 <= index < len(ids)) or not all(
            isinstance(i, str) for i in ids
        ):
            return False
        self._ordered_shuffle = bool(state.get("shuffle", True))
        queue, new_index = self._merge(ids, index)
        if new_index < 0:
            return False
        self._set_queue(queue)
        self.current_index = -1
        try:
            position = float(state.get("position") or 0)
        except (TypeError, ValueError):
            position = 0.0
        if queue[new_index].id != ids[index]:
            position = 0.0  # der Titel wurde gelöscht, es geht mit dem nächsten weiter
        self._resume = (new_index, position)
        return True

    def _save_queue(self, position: float = 0.0) -> None:
        """Für start_mode "resume". Laufen gerade Suchtreffer, wird gespeichert,
        was danach käme: die restlichen Treffer, dann die übrige Bibliothek."""
        if self.current_index < 0 or not self.queue:
            return
        if self.search_active:
            rest = self.queue[self.current_index :]
            rest_ids = {t.id for t in rest}
            order = rest + [t for t in self._arrange(self.all_tracks) if t.id not in rest_ids]
            index = 0
        else:
            order, index = self.queue, self.current_index
        self.client.save_queue([t.id for t in order], index, position, self._ordered_shuffle)

    # --- Tabellen ---------------------------------------------------------

    def _fill(self, table: TrackTable, tracks: List[Track]) -> dict:
        """Zeigt `tracks` in `table`; gibt id(Titel-Zelle) -> Titel-ID zurück."""
        table.clear()
        cells = {}
        # Text statt str: DataTable würde str als Markup lesen und z.B.
        # "[feat. Juice WRLD]" verschlucken; "ellipsis" kürzt mit "…".
        for t in tracks:
            title = Text(t.title, overflow="ellipsis")
            cells[id(title)] = t.id
            table.add_row(
                title,
                Text(t.artist, overflow="ellipsis"),
                Text(t.album, overflow="ellipsis"),
                t.duration_str,
                key=t.id,
            )
        table.set_wanted_widths(
            [
                typical_width((t.title for t in tracks), "Titel"),
                typical_width((t.artist for t in tracks), "Interpret"),
                typical_width((t.album for t in tracks), "Album"),
                max(len("Dauer"), max((len(t.duration_str) for t in tracks), default=0)),
            ]
        )
        return cells

    def _update_titles(self) -> None:
        self.tracks_table.border_title = (
            "Alle Titel (zufällige Reihenfolge)" if self._ordered_shuffle else "Alle Titel (sortiert)"
        )
        if self.search_active and self.hits is self.queue:
            title = f"Suchtreffer ({len(self.queue)}) laufen als Queue"
        elif self._hits_total > len(self.hits):
            title = f"Suche: die ersten {len(self.hits)} von {self._hits_total} Treffern"
        else:
            title = f"Suche: {len(self.hits)} Treffer"
        self.hits_table.border_title = title

    def _hits_shown(self) -> bool:
        return self.main.query_one("#list-panel").has_class("searching")

    def _show_hits(self, hits: List[Track]) -> None:
        if hits is self.queue:
            self._hits_total = len(hits)
        else:
            self._hits_total = len(hits)
            hits = hits[:MAX_HITS]
        self.hits = hits
        self._fill(self.hits_table, hits)
        self.main.query_one("#list-panel").add_class("searching")
        self._follow_row = -1
        self._update_titles()
        if hits is self.queue and self.current_index >= 0:
            self._follow(self.hits_table, self.current_index)

    def _hide_hits(self) -> None:
        if not self._hits_shown():
            return
        self.main.query_one("#list-panel").remove_class("searching")
        if self.focused is self.hits_table:
            self.tracks_table.focus()
        self._follow_row = -1
        if not self.search_active and self.current_index >= 0:
            self._follow(self.tracks_table, self.current_index)

    def _visible_table(self) -> TrackTable:
        return self.hits_table if self._hits_shown() else self.tracks_table

    def _queue_table(self) -> Optional[TrackTable]:
        """Die Tabelle, in der die Queue gerade steht (None: in keiner)."""
        if not self.search_active:
            return self.tracks_table
        if self._hits_shown() and self.hits is self.queue:
            return self.hits_table
        return None

    def _follow(self, table: TrackTable, index: int) -> None:
        """Cursor zum laufenden Titel - aber nur, wenn er vorher beim alten
        stand und der zu sehen war: wer gerade durch die Liste blättert, wird
        nicht weggerissen ([g] holt ihn zurück)."""
        if self._follow_row >= 0:
            row = table.cursor_row
            if row not in (self._follow_row, index):
                return  # Cursor wurde bewegt
            if row != index and not self._row_visible(table, row):
                return  # weggescrollt
        table.move_cursor(row=index)
        self._follow_row = index

    @staticmethod
    def _row_visible(table: TrackTable, row: int) -> bool:
        height = table.scrollable_content_region.height - (1 if table.show_header else 0)
        if height <= 0:
            return True  # (gerade nicht angezeigt)
        top = round(table.scroll_y)
        return top <= row < top + height

    def _refresh_queue_panel(self) -> None:
        lv = self.main.query_one("#queue-list", ListView)
        lv.clear()
        upcoming = self.queue[self.current_index + 1 : self.current_index + 21]
        for t in upcoming:
            lv.append(ListItem(Label(f"{t.title} - {t.artist}", markup=False)))

    # --- Wiedergabe -------------------------------------------------------

    def _play_track(self, index: int) -> None:
        if not (0 <= index < len(self.queue)) or self.player is None:
            return
        start = self._resume[1] if self._resume and self._resume[0] == index else None
        self._resume = None
        self.current_index = index
        track = self.queue[index]
        self.player.load(self.client.stream_url(track.id), start=start)
        self._pos_sample = (start or 0.0, time.monotonic(), False)
        self._update_neighbors()
        self._show_current()

    def _update_neighbors(self) -> None:
        i = self.current_index
        prev_url = self.client.stream_url(self.queue[i - 1].id) if i > 0 else None
        next_url = (
            self.client.stream_url(self.queue[i + 1].id)
            if i + 1 < len(self.queue)
            else None
        )
        self.player.set_neighbors(prev_url, next_url)

    def _show_current(self) -> None:
        track = self.queue[self.current_index]
        self._set_status(f"♪ {track.title}", f"{track.artist} — {track.album}")
        table = self._queue_table()
        if table is not None:
            self._follow(table, self.current_index)
        self._refresh_queue_panel()
        self._load_extras()
        self._save_queue()

    def _play_next(self) -> None:
        if self.current_index + 1 >= len(self.queue):
            # Durch - neu mischen statt zu stoppen (Endlos-Zufallswiedergabe).
            # Nach Suchtreffern geht es mit der ganzen Bibliothek weiter.
            self.search_active = False
            self._pending_update = False
            self._reshuffle(self.all_tracks)
        self._play_track(self.current_index + 1)

    def _play_prev(self) -> None:
        if self.current_index > 0:
            self._play_track(self.current_index - 1)

    def _refresh_status(self, st: Optional[dict] = None) -> Optional[dict]:
        """Zeit, Pause-Knopf und Lautstärke anzeigen - auch wenn sie über
        Medientasten oder das KDE/GNOME-Widget geändert wurden."""
        if self.player is None:
            return None
        if st is None:
            st = self.player.status()
        pos, dur, paused = st["time-pos"] or 0.0, st["duration"] or 0.0, bool(st["pause"])
        self._pos_sample = (pos, time.monotonic(), paused)
        self.main.query_one(SeekBar).set_progress(pos, dur)
        self.main.query_one("#btn-play", Control).set_label("▶" if paused else "⏸")
        self.main.query_one(VolumeBar).set_volume(st["volume"] or 0.0)
        if self.presence and self.current_index >= 0 and not self._stalled:
            self.presence.update(self.queue[self.current_index], pos, dur, paused)
        return st

    def _estimated_position(self) -> float:
        """Position ohne mpv zu fragen: letzte Abfrage plus die Zeit seitdem."""
        pos, at, paused = self._pos_sample
        return pos if paused else pos + time.monotonic() - at

    def _tick(self) -> None:
        if self.player is None or self._finished:
            return
        if not self.player.alive:
            if not self._mpv_gone:
                self._mpv_gone = True
                self._set_status(
                    "⚠ mpv wurde unerwartet beendet",
                    f"Details stehen in {MPVPlayer.LOG_PATH} - bitte den Player neu starten",
                )
            return
        st = self._refresh_status()
        if self.player.failures >= MAX_FAILURES and self.current_index >= 0:
            self._while_stalled(st)
            return
        if self._stalled:
            # Lädt wieder: Nachbarn nachtragen, Cover und Songtext neu holen
            self._stalled = False
            self._update_neighbors()
            self._show_current()
            self._load_extras(force=True)
            return
        if st["idle-active"] and self.current_index >= 0:
            self._play_next()
            return
        # mpv hat selbst den Titel gewechselt (Titelende oder Weiter/Zurück
        # über Medientasten bzw. KDE/GNOME-Widget) -> Anzeige nachziehen.
        step = self.player.playlist_step(st["playlist-pos"])
        if step:
            self.current_index += step
            self._pos_sample = (0.0, time.monotonic(), False)
            self._update_neighbors()
            self._show_current()

    def _while_stalled(self, st: dict) -> None:
        """Mehrere Titel in Folge ließen sich nicht laden (Server weg, VPN
        getrennt, Anmeldung widerrufen): anhalten, statt im Sekundentakt durch
        die Bibliothek zu springen, und regelmäßig neu versuchen."""
        now = time.monotonic()
        if not self._stalled:
            self._stalled = True
            self._retry_at = now + RETRY_SECONDS
            if self.presence:
                self.presence.clear()
        self._set_status(
            "⚠ Angehalten: die Titel lassen sich nicht laden (Server nicht erreichbar?)",
            f"Neuer Versuch alle {RETRY_SECONDS} s - Leertaste: jetzt, n: nächster Titel",
        )
        if now >= self._retry_at and st["idle-active"]:
            self._retry_at = now + RETRY_SECONDS
            self.player.load(self.client.stream_url(self.queue[self.current_index].id))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Klick (oder Enter) auf einen Titel spielt ihn ab."""
        self._select(event.data_table, event.cursor_row)

    def _select(self, table: DataTable, row: int) -> None:
        if not 0 <= row < table.row_count:
            return
        if table is self.hits_table:
            if self.hits is not self.queue:
                # Treffer angeklickt: ab jetzt laufen die Treffer als Queue
                self.queue = self.hits
                self.search_active = True
                self.current_index = -1
                self._resume = None
                self._update_titles()
            elif row == self.current_index:
                return  # doppelte Meldung für denselben Klick
        elif self.search_active or row == self.current_index:
            return
        self._play_track(row)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Klick (oder Enter) auf einen Titel in der Queue spielt ihn ab."""
        if event.list_view.index is not None:
            self._play_track(self.current_index + 1 + event.list_view.index)

    def on_seek_bar_seek(self, event: SeekBar.Seek) -> None:
        if self.player:
            self.player.seek(event.seconds, relative=False)
            self._refresh_status()

    def on_volume_bar_changed(self, event: VolumeBar.Changed) -> None:
        if self.player:
            self.player.set_volume(event.volume)
            self._refresh_status()

    def action_toggle_pause(self) -> None:
        if self.player is None:
            return
        if self._stalled:
            self._retry_at = 0.0
            self._tick()
        elif self.current_index < 0:
            # Autoplay war aus: Leertaste startet bei der markierten Zeile
            table = self._visible_table()
            self._select(table, table.cursor_row)
        else:
            self.player.play_pause()
            self._refresh_status()

    def action_next_track(self) -> None:
        self._play_next()

    def action_prev_track(self) -> None:
        self._play_prev()

    def action_seek_back(self) -> None:
        if self.player:
            self.player.seek(-10)
            self._refresh_status()

    def action_seek_fwd(self) -> None:
        if self.player:
            self.player.seek(10)
            self._refresh_status()

    def action_reshuffle(self) -> None:
        if not self.all_tracks:
            self._start_loading()  # Laden ist vermutlich fehlgeschlagen -> erneut versuchen
            return
        if self._search_open():
            self._close_search_ui()
            self._hide_hits()
        self.search_active = False
        self._pending_update = False
        self._reshuffle(self.all_tracks, shuffle=True)
        self._play_next()

    def action_goto_current(self) -> None:
        table = self._queue_table()
        if table is not None and self.current_index >= 0:
            self._follow_row = -1
            self._follow(table, self.current_index)

    # --- Cover und Songtext -----------------------------------------------

    @property
    def show_cover(self) -> bool:
        return bool(self.config.get("show_cover", True)) and cover_supported()

    @property
    def show_lyrics(self) -> bool:
        return bool(self.config.get("show_lyrics", True))

    def _apply_view(self) -> None:
        """Cover und Songtext je nach Einstellung ein- bzw. ausblenden."""
        self.main.query_one("#now-playing").set_class(self.show_cover, "with-cover")
        self.main.query_one("#side").set_class(self.show_lyrics, "with-lyrics")
        self._load_extras(force=True)

    def _toggle_setting(self, key: str) -> None:
        self.config[key] = not self.config.get(key, True)
        try:
            if self._save_config:
                self._save_config()
        except OSError as exc:
            self.notify(f"Speichern fehlgeschlagen: {exc}", severity="error")
        self._apply_view()

    def action_toggle_lyrics(self) -> None:
        if not isinstance(self.screen, SettingsScreen):
            self._toggle_setting("show_lyrics")

    def action_toggle_cover(self) -> None:
        if isinstance(self.screen, SettingsScreen):
            return
        if not cover_supported():
            self.notify("Für Cover fehlt Pillow:  pip install pillow", severity="warning")
            return
        self._toggle_setting("show_cover")

    def _load_extras(self, force: bool = False) -> None:
        """Cover und Songtext des laufenden Titels holen (nur was sichtbar ist)."""
        if not 0 <= self.current_index < len(self.queue):
            return
        track = self.queue[self.current_index]
        if track.id == self._extras_for and not force:
            return
        self._extras_for = track.id
        self._set_lyrics([])
        if self.show_lyrics:
            self._show_lyrics_text("Lade Songtext ...")
            self.run_worker(
                lambda: self._fetch_lyrics(track), thread=True, exit_on_error=False,
                group="lyrics", exclusive=True,
            )
        if self.show_cover:
            cover_id = track.album_id or track.id
            if cover_id in self._covers:
                self.main.query_one(CoverArt).set_art(self._covers[cover_id])
            else:
                self.main.query_one(CoverArt).set_art(None)
                self.run_worker(
                    lambda: self._fetch_cover(track, cover_id), thread=True,
                    exit_on_error=False, group="cover", exclusive=True,
                )

    def _is_current(self, track: Track) -> bool:
        return 0 <= self.current_index < len(self.queue) and self.queue[self.current_index].id == track.id

    def _fetch_cover(self, track: Track, cover_id: str) -> None:
        try:
            data = self.client.fetch_cover(cover_id)
        except Exception:
            return  # dann eben ohne Cover
        art = render_cover(data) if data else None
        self.call_from_thread(self._on_cover, track, cover_id, art)

    def _on_cover(self, track: Track, cover_id: str, art) -> None:
        if len(self._covers) >= 16:
            self._covers.pop(next(iter(self._covers)))
        self._covers[cover_id] = art
        if self._is_current(track):
            self.main.query_one(CoverArt).set_art(art)

    def _fetch_lyrics(self, track: Track) -> None:
        try:
            lines = self.client.fetch_lyrics(track.id)
        except Exception:
            lines = []
        self.call_from_thread(self._on_lyrics, track, lines)

    def _set_lyrics(self, lines: list) -> None:
        self._lyrics, self._lyric_line, self._lyric_rows = lines, -1, None
        self._lyrics_synced = bool(lines) and all(start is not None for start, _ in lines)
        self._lyric_starts = [start for start, _ in lines] if self._lyrics_synced else []

    def _on_lyrics(self, track: Track, lines: list) -> None:
        if not self._is_current(track):
            return
        self._set_lyrics(lines)
        if not lines:
            self._show_lyrics_text("Kein Songtext vorhanden.")
            return
        self._render_lyrics()
        self._sync_lyrics()

    def _show_lyrics_text(self, message: str) -> None:
        self.main.query_one("#lyrics-text", Static).update(Text(message, style="dim"))

    def _render_lyrics(self) -> None:
        text = Text()
        for i, (_, line) in enumerate(self._lyrics):
            if i:
                text.append("\n")
            text.append(line, style=f"bold {ACCENT}" if i == self._lyric_line else "")
        self.main.query_one("#lyrics-text", Static).update(text)

    def _sync_lyrics(self) -> None:
        """Bei zeitgenau hinterlegtem Songtext die aktuelle Zeile hervorheben
        und in die Mitte scrollen. Läuft 4x pro Sekunde, fragt dafür aber
        nicht mpv: die Position wird aus der letzten Abfrage hochgerechnet."""
        if not (self._lyrics_synced and self.show_lyrics and self.player):
            return
        line = bisect.bisect_right(self._lyric_starts, self._estimated_position()) - 1
        if line == self._lyric_line:
            return
        self._lyric_line = line
        self._render_lyrics()
        panel = self.main.query_one("#lyrics-panel", VerticalScroll)
        rows = self._lyric_row_starts(self.main.query_one("#lyrics-text").size.width)
        y = rows[line] - panel.scrollable_content_region.height // 2 + 1 if line >= 0 else 0
        panel.scroll_to(y=max(0, y), animate=False)

    def _lyric_row_starts(self, width: int) -> List[int]:
        """Bildschirmzeile, in der jede Songtextzeile anfängt - lange Zeilen
        brechen in der schmalen Spalte um und brauchen mehrere."""
        if self._lyric_rows and self._lyric_rows[0] == width:
            return self._lyric_rows[1]
        starts, y = [], 0
        for _, line in self._lyrics:
            starts.append(y)
            y += max(1, len(Text(line).wrap(self.console, max(1, width))))
        self._lyric_rows = (width, starts)
        return starts

    # --- Suche ------------------------------------------------------------

    def _search_open(self) -> bool:
        return self.main.query_one("#search", Input).has_class("visible")

    def action_search(self) -> None:
        if not self.all_tracks or isinstance(self.screen, SettingsScreen):
            return
        search = self.main.query_one("#search", Input)
        if self.focused is search:
            raise SkipAction()  # "/" im Suchfeld ist ein normales Zeichen
        search.disabled = False
        search.add_class("visible")
        search.focus()

    def _close_search_ui(self) -> None:
        if self._search_timer is not None:
            self._search_timer.stop()
        search = self.main.query_one("#search", Input)
        search.value = ""
        search.remove_class("visible")
        search.disabled = True

    def action_close_search(self) -> None:
        if not self._search_open():
            return
        self._close_search_ui()
        self._end_search()
        self.tracks_table.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if not self._search_open():
            return  # (das Leeren beim Schließen)
        # Kurz warten, damit nicht jeder Tastenanschlag die Treffer neu aufbaut
        if self._search_timer is not None:
            self._search_timer.stop()
        self._search_timer = self.set_timer(0.2, lambda: self._apply_search(event.value))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._visible_table().focus()

    def _apply_search(self, text: str) -> None:
        words = text.casefold().split()
        if not words:
            if not self.search_active:
                self._hide_hits()
            elif self.hits is not self.queue or not self._hits_shown():
                self._show_hits(self.queue)  # die laufenden Treffer wieder zeigen
            return
        self._show_hits([t for t, hay in self._haystack if all(w in hay for w in words)])

    def _end_search(self) -> None:
        """Suche zu: die große Tabelle ist wieder da. Liefen Treffer, wird die
        Bibliothek mit dem laufenden als erstem Titel neu geordnet."""
        if self.search_active:
            self.search_active = False
            current = self.queue[self.current_index] if self.current_index >= 0 else None
            self._pending_update = False
            self._reshuffle(self.all_tracks, first=current)
            self._hide_hits()
            if current is not None:
                self.current_index = 0
                self._update_neighbors()
                self._show_current()
            return
        self._hide_hits()
        if self._pending_update:
            self._apply_library_update()

    # --- Einstellungen und Lautstärke --------------------------------------

    def action_settings(self) -> None:
        if isinstance(self.screen, SettingsScreen):
            return
        current = {
            "autoplay": self.config.get("autoplay", True),
            "show_cover": self.config.get("show_cover", True),
            "show_lyrics": self.config.get("show_lyrics", True),
            "start_mode": self.config.get("start_mode", "shuffle"),
            "replaygain": self.config.get("replaygain", "track"),
        }

        def done(result: Optional[dict]) -> None:
            if result is None:
                return
            self.config.update(result)
            try:
                if self._save_config:
                    self._save_config()
            except OSError as exc:
                self.notify(f"Speichern fehlgeschlagen: {exc}", severity="error")
                return
            self._apply_view()
            if self.player:
                self.player.set_replaygain(result["replaygain"])
            self.notify("Gespeichert (Autoplay und Reihenfolge gelten ab dem nächsten Start).")

        self.push_screen(SettingsScreen(current), done)

    def action_vol_up(self) -> None:
        if self.player:
            self.player.change_volume(5)
            self._refresh_status()

    def action_vol_down(self) -> None:
        if self.player:
            self.player.change_volume(-5)
            self._refresh_status()
