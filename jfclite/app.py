from __future__ import annotations

from typing import List, Optional

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    Static,
)

from . import NAME
from .client import JellyfinClient, Track, shuffled
from .discord import DiscordPresence
from .player import MPVPlayer
from .widgets import Control, SeekBar, TrackTable, VolumeBar, typical_width

ACCENT = "#e63946"  # rot

CSS = f"""
Screen {{
    background: transparent;
}}

#main {{
    height: 1fr;
    background: transparent;
}}

#tracks {{
    width: 1fr;
    overflow-x: hidden;
    border: round {ACCENT};
    border-title-color: {ACCENT};
    background: transparent;
}}

#queue-panel {{
    width: 25%;
    min-width: 26;
    max-width: 40;
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
"""


class JellyfinClientLite(App):
    CSS = CSS
    TITLE = NAME
    BINDINGS = [
        ("space", "toggle_pause", "Play/Pause"),
        ("n", "next_track", "Nächster Titel"),
        ("p", "prev_track", "Vorheriger Titel"),
        ("left", "seek_back", "-10s"),
        ("right", "seek_fwd", "+10s"),
        ("r", "reshuffle", "Neu mischen"),
        ("plus", "vol_up", "Lauter"),
        Binding("=", "vol_up", "Lauter", show=False),  # US-Layout
        ("-", "vol_down", "Leiser"),
        ("q", "quit", "Beenden"),
    ]

    def __init__(
        self, client: JellyfinClient, presence: Optional[DiscordPresence] = None
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
        self.queue: List[Track] = []
        self.current_index: int = -1

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            table = TrackTable(id="tracks", cursor_type="row")
            table.border_title = "Alle Titel (zufällige Reihenfolge)"
            table.border_subtitle = "Klick oder Enter = abspielen"
            yield table
            with Vertical(id="queue-panel"):
                yield Label("Queue", id="queue-label")
                yield ListView(id="queue-list")
        with Vertical(id="now-playing"):
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
        table = self.query_one("#tracks", DataTable)
        table.add_columns("Titel", "Interpret", "Album", "Dauer")
        self.query_one("#np-title", Static).update("Lade Bibliothek ...")
        self.query_one("#np-sub", Static).update(
            "Kann bei großen Bibliotheken oder über VPN/Tunnel einen Moment dauern."
        )
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

    def _on_library_error(self, exc: Exception) -> None:
        self.query_one("#np-title", Static).update("⚠ Bibliothek konnte nicht geladen werden")
        self.query_one("#np-sub", Static).update(
            f"{type(exc).__name__}: {exc}  —  [r] zum erneuten Versuch"
        )

    def _on_library_loaded(self, tracks: List[Track]) -> None:
        if not tracks:
            self.query_one("#np-title", Static).update(
                "Keine Titel in der Jellyfin-Bibliothek gefunden."
            )
            return
        self._reshuffle(tracks)
        if self.player is None:
            self.player = MPVPlayer()
            self.player.set_http_headers(self.client.stream_headers())
        self._play_next()
        self.set_interval(1.0, self._tick)

    def _on_library_updated(self, tracks: List[Track]) -> None:
        """Bibliothek hat sich auf dem Server geändert: neue Liste übernehmen,
        ohne den laufenden Titel zu unterbrechen."""
        if not tracks or self.current_index < 0:
            return
        current = self.queue[self.current_index]
        self._reshuffle(tracks, first=current)
        self.current_index = 0
        self._update_neighbors()
        self._show_current()
        self.notify(f"Bibliothek aktualisiert: {len(tracks)} Titel")

    def _reshuffle(self, tracks: List[Track], first: Optional[Track] = None) -> None:
        """Mischt neu und baut die Tabelle in der neuen Reihenfolge auf -
        sonst passt die markierte Zeile nicht mehr zum laufenden Titel."""
        self.queue = shuffled(tracks)
        if first is not None:
            self.queue = [first] + [t for t in self.queue if t.id != first.id]
        self.current_index = -1
        table = self.query_one(TrackTable)
        table.clear()
        # Text statt str: DataTable würde str als Markup lesen und z.B.
        # "[feat. Juice WRLD]" verschlucken; "ellipsis" kürzt mit "…".
        for t in self.queue:
            table.add_row(
                Text(t.title, overflow="ellipsis"),
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

    def _refresh_queue_panel(self) -> None:
        lv = self.query_one("#queue-list", ListView)
        lv.clear()
        upcoming = self.queue[self.current_index + 1 : self.current_index + 21]
        for t in upcoming:
            lv.append(ListItem(Label(f"{t.title} - {t.artist}", markup=False)))

    def _play_track(self, index: int) -> None:
        if not (0 <= index < len(self.queue)) or self.player is None:
            return
        self.current_index = index
        track = self.queue[index]
        self.player.load(self.client.stream_url(track.id))
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
        index = self.current_index
        track = self.queue[index]
        self.query_one("#np-title", Static).update(f"♪ {track.title}")
        self.query_one("#np-sub", Static).update(
            f"{track.artist} — {track.album}"
        )
        table = self.query_one("#tracks", DataTable)
        table.move_cursor(row=index)
        self._refresh_queue_panel()

    def _play_next(self) -> None:
        if self.current_index + 1 >= len(self.queue):
            # Bibliothek durch - neu mischen statt zu stoppen (Endlos-Zufallswiedergabe)
            self._reshuffle(self.queue)
        self._play_track(self.current_index + 1)

    def _play_prev(self) -> None:
        if self.current_index > 0:
            self._play_track(self.current_index - 1)

    def _refresh_status(self) -> None:
        """Zeit, Pause-Knopf und Lautstärke anzeigen - auch wenn sie über
        Medientasten oder das KDE/GNOME-Widget geändert wurden."""
        if self.player is None:
            return
        pos, dur, paused = self.player.position, self.player.duration, self.player.is_paused
        self.query_one(SeekBar).set_progress(pos, dur)
        self.query_one("#btn-play", Control).set_label("▶" if paused else "⏸")
        self.query_one(VolumeBar).set_volume(self.player.volume)
        if self.presence and self.current_index >= 0:
            self.presence.update(self.queue[self.current_index], pos, dur, paused)

    def _tick(self) -> None:
        if self.player is None:
            return
        self._refresh_status()
        if self.player.is_idle and self.current_index >= 0:
            self._play_next()
            return
        # mpv hat selbst den Titel gewechselt (Titelende oder Weiter/Zurück
        # über Medientasten bzw. KDE/GNOME-Widget) -> Anzeige nachziehen.
        step = self.player.playlist_step()
        if step:
            self.current_index += step
            self._update_neighbors()
            self._show_current()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Klick (oder Enter) auf einen Titel spielt ihn ab."""
        if event.cursor_row != self.current_index:
            self._play_track(event.cursor_row)

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
        if self.player:
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
        if not self.queue:
            # Laden ist vermutlich fehlgeschlagen -> erneut versuchen
            self.run_worker(self._load_library, thread=True, exit_on_error=False)
            return
        self._reshuffle(self.queue)
        self._play_next()

    def action_vol_up(self) -> None:
        if self.player:
            self.player.change_volume(5)
            self._refresh_status()

    def action_vol_down(self) -> None:
        if self.player:
            self.player.change_volume(-5)
            self._refresh_status()

    def on_unmount(self) -> None:
        if self.presence:
            self.presence.close()
        if self.player:
            self.player.shutdown()
