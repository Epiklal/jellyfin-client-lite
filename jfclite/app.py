from __future__ import annotations

from typing import Callable, List, Optional

from rich.text import Text
from textual.actions import SkipAction
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
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

from . import NAME
from .client import JellyfinClient, Track, shuffled, sorted_tracks
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

#tracks {{
    height: 1fr;
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

SettingsScreen {{
    align: center middle;
}}

#settings {{
    width: 60;
    height: auto;
    border: round {ACCENT};
    padding: 1 2;
    background: $surface;
}}

#settings-title {{
    color: {ACCENT};
    text-style: bold;
    margin-bottom: 1;
}}

#settings Horizontal {{
    height: auto;
    margin-bottom: 1;
}}

#settings Horizontal Label {{
    width: 1fr;
    padding-top: 1;
}}

#settings-buttons {{
    align-horizontal: right;
    margin-bottom: 0;
}}

#settings-buttons Button {{
    margin-left: 1;
}}
"""

START_MODES = [("Zufällig gemischt", "shuffle"), ("Sortiert (Interpret, Album)", "sorted")]


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
                yield Label("Beim Start: Reihenfolge")
                yield Select(
                    START_MODES,
                    value=self._settings["start_mode"],
                    allow_blank=False,
                    id="set-start-mode",
                )
            with Horizontal(id="settings-buttons"):
                yield Button("Abbrechen", id="cancel")
                yield Button("Speichern", id="save", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.dismiss(
                {
                    "autoplay": self.query_one("#set-autoplay", Switch).value,
                    "start_mode": self.query_one("#set-start-mode", Select).value,
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
        ("s", "settings", "Einstellungen"),
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
        self.queue: List[Track] = []
        self.current_index: int = -1
        self.config = config if config is not None else {}
        self._save_config = save_config
        self.all_tracks: List[Track] = []
        self.shown = self.queue  # was die Tabelle zeigt: die Queue oder Suchtreffer
        self._ordered_shuffle = True
        self.search_active = False  # Queue besteht nur aus Suchtreffern
        self._search_timer = None

    @property
    def main(self):
        """Der Hauptbildschirm - auch dann, wenn gerade ein Dialog darüberliegt
        (die Wiedergabe läuft ja weiter und aktualisiert ihre Anzeige)."""
        return self.screen_stack[0]

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
                table = TrackTable(id="tracks", cursor_type="row")
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
        table = self.main.query_one("#tracks", DataTable)
        table.add_columns("Titel", "Interpret", "Album", "Dauer")
        self._update_title()
        self.main.query_one("#np-title", Static).update("Lade Bibliothek ...")
        self.main.query_one("#np-sub", Static).update(
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
        self.main.query_one("#np-title", Static).update("⚠ Bibliothek konnte nicht geladen werden")
        self.main.query_one("#np-sub", Static).update(
            f"{type(exc).__name__}: {exc}  —  [r] zum erneuten Versuch"
        )

    def _on_library_loaded(self, tracks: List[Track]) -> None:
        if not tracks:
            self.main.query_one("#np-title", Static).update(
                "Keine Titel in der Jellyfin-Bibliothek gefunden."
            )
            return
        self.all_tracks = tracks
        self._reshuffle(tracks)
        if self.player is None:
            self.player = MPVPlayer()
            self.player.set_http_headers(self.client.stream_headers())
        if self.config.get("autoplay", True):
            self._play_next()
        else:
            self.main.query_one("#np-title", Static).update(
                f"{len(tracks)} Titel geladen - Leertaste oder Enter zum Abspielen"
            )
            self.main.query_one("#np-sub", Static).update("")
        self.set_interval(1.0, self._tick)

    def _on_library_updated(self, tracks: List[Track]) -> None:
        """Bibliothek hat sich auf dem Server geändert: neue Liste übernehmen,
        ohne den laufenden Titel zu unterbrechen."""
        if not tracks:
            return
        self.all_tracks = tracks
        if self.current_index < 0:
            self._reshuffle(tracks)  # es läuft noch nichts (Autoplay aus)
            self.notify(f"Bibliothek aktualisiert: {len(tracks)} Titel")
            return
        current = self.queue[self.current_index]
        self._reshuffle(tracks, first=current)
        self.current_index = 0
        self._update_neighbors()
        self._show_current()
        self.notify(f"Bibliothek aktualisiert: {len(tracks)} Titel")

    def _reshuffle(
        self,
        tracks: List[Track],
        first: Optional[Track] = None,
        shuffle: Optional[bool] = None,
    ) -> None:
        """Ordnet neu (gemischt oder sortiert, je nach Einstellung) und baut die
        Tabelle in der neuen Reihenfolge auf - sonst passt die markierte Zeile
        nicht mehr zum laufenden Titel. Beendet dabei auch eine Suche."""
        self._ordered_shuffle = (
            self.config.get("start_mode", "shuffle") == "shuffle" if shuffle is None else shuffle
        )
        self.queue = shuffled(tracks) if self._ordered_shuffle else sorted_tracks(tracks)
        if first is not None:
            self.queue = [first] + [t for t in self.queue if t.id != first.id]
        self.current_index = -1
        self.search_active = False
        search = self.main.query_one("#search", Input)
        if search.value:
            search.value = ""
        search.remove_class("visible")
        search.disabled = True
        self._fill_table(self.queue)

    def _fill_table(self, tracks: List[Track]) -> None:
        """Zeigt `tracks` in der Tabelle (Queue oder Suchtreffer)."""
        self.shown = tracks
        self._update_title()
        table = self.main.query_one(TrackTable)
        table.clear()
        # Text statt str: DataTable würde str als Markup lesen und z.B.
        # "[feat. Juice WRLD]" verschlucken; "ellipsis" kürzt mit "…".
        for t in tracks:
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

    def _update_title(self) -> None:
        table = self.main.query_one(TrackTable)
        if self.shown is not self.queue:
            table.border_title = f"Suche: {len(self.shown)} Treffer"
        elif self.search_active:
            table.border_title = f"Suchtreffer ({len(self.queue)})"
        elif self._ordered_shuffle:
            table.border_title = "Alle Titel (zufällige Reihenfolge)"
        else:
            table.border_title = "Alle Titel (sortiert)"

    def _refresh_queue_panel(self) -> None:
        lv = self.main.query_one("#queue-list", ListView)
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
        self.main.query_one("#np-title", Static).update(f"♪ {track.title}")
        self.main.query_one("#np-sub", Static).update(
            f"{track.artist} — {track.album}"
        )
        if self.shown is self.queue:
            self.main.query_one("#tracks", DataTable).move_cursor(row=index)
        self._refresh_queue_panel()

    def _play_next(self) -> None:
        if self.current_index + 1 >= len(self.queue):
            # Bibliothek durch - neu mischen statt zu stoppen (Endlos-Zufallswiedergabe)
            self._reshuffle(self.all_tracks if self.search_active else self.queue)
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
        self.main.query_one(SeekBar).set_progress(pos, dur)
        self.main.query_one("#btn-play", Control).set_label("▶" if paused else "⏸")
        self.main.query_one(VolumeBar).set_volume(self.player.volume)
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
        row = event.cursor_row
        if self.shown is not self.queue:
            # Treffer angeklickt: ab jetzt laufen die Treffer als Queue
            self.queue = self.shown
            self.search_active = True
            self.current_index = -1
            self._update_title()
        elif row == self.current_index:
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
        if self.player and self.current_index < 0 and self.queue:
            # Autoplay war aus: Leertaste startet bei der markierten Zeile
            table = self.main.query_one(TrackTable)
            self._play_track(table.cursor_row if self.shown is self.queue else 0)
        elif self.player:
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
        self._reshuffle(self.all_tracks if self.search_active else self.queue, shuffle=True)
        self._play_next()

    def action_search(self) -> None:
        if not self.all_tracks or isinstance(self.screen, SettingsScreen):
            return
        search = self.main.query_one("#search", Input)
        if self.focused is search:
            raise SkipAction()  # "/" im Suchfeld ist ein normales Zeichen
        search.disabled = False
        search.add_class("visible")
        search.focus()

    def action_close_search(self) -> None:
        search = self.main.query_one("#search", Input)
        if not search.has_class("visible"):
            return
        if self._search_timer is not None:
            self._search_timer.stop()
        search.value = ""
        search.remove_class("visible")
        search.disabled = True
        self._end_search()
        self.main.query_one(TrackTable).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        # Kurz warten, damit nicht jeder Tastenanschlag 3000 Zeilen neu aufbaut
        if self._search_timer is not None:
            self._search_timer.stop()
        self._search_timer = self.set_timer(0.2, lambda: self._apply_search(event.value))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.main.query_one(TrackTable).focus()

    def _apply_search(self, text: str) -> None:
        words = text.casefold().split()
        if not words:
            self._end_search()
            return
        hits = [
            t
            for t in self.all_tracks
            if all(w in f"{t.title} {t.artist} {t.album}".casefold() for w in words)
        ]
        self._fill_table(hits)

    def _end_search(self) -> None:
        """Suche beendet: Tabelle zeigt wieder die ganze Queue. Lief ein Treffer,
        wird die Bibliothek mit ihm als erstem Titel neu geordnet."""
        if self.search_active:
            current = self.queue[self.current_index] if self.current_index >= 0 else None
            self._reshuffle(self.all_tracks, first=current)
            if current is not None:
                self.current_index = 0
                self._update_neighbors()
                self._show_current()
        elif self.shown is not self.queue:
            self._fill_table(self.queue)
            if self.current_index >= 0:
                self.main.query_one(TrackTable).move_cursor(row=self.current_index)

    def action_settings(self) -> None:
        if isinstance(self.screen, SettingsScreen):
            return
        current = {
            "autoplay": self.config.get("autoplay", True),
            "start_mode": self.config.get("start_mode", "shuffle"),
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
            self.notify("Gespeichert (gilt ab dem nächsten Start).")

        self.push_screen(SettingsScreen(current), done)

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
