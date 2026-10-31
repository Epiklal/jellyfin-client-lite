from __future__ import annotations

import time
from typing import Callable, List, Optional

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

from . import NAME
from .client import JellyfinClient, Track, album_tracks, shuffled, sorted_tracks
from .discord import DiscordPresence
from .i18n import t
from .player import MPVPlayer, replaygain_args
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
# So viele Titel dürfen hintereinander scheitern, bevor die Wiedergabe anhält -
# sonst rauscht der Player bei einem Server-Neustart durch die ganze Bibliothek.
MAX_FAILURES = 5
# So oft pro Minute wird ein abgestürztes mpv von selbst neu gestartet - stürzt
# es öfter ab (z.B. an jedem Titel), bleibt es aus statt endlos neu zu starten.
MAX_RESTARTS = 3

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

#settings {{
    width: 66;
    height: auto;
    border: round {ACCENT};
    padding: 0 2;
    background: $surface;
}}

#settings-title {{
    color: {ACCENT};
    text-style: bold;
}}

#settings Horizontal {{
    height: auto;
}}

#settings Horizontal Label {{
    width: 1fr;
    padding-top: 1;
}}

/* Breit genug, dass kein Eintrag umbricht: sonst passt der Dialog nicht in 24 Zeilen */
#settings Select {{
    width: 36;
}}

#settings-buttons {{
    align-horizontal: right;
}}

#settings-buttons Button {{
    margin-left: 1;
}}
"""

START_MODES = [(t("Zufällig gemischt"), "shuffle"), (t("Sortiert (Interpret, Album)"), "sorted")]
LANGUAGES = [(t("Automatisch"), "auto"), ("Deutsch", "de"), ("English", "en")]


class SettingsScreen(ModalScreen[Optional[dict]]):
    """Einstellungen; Ergebnis ist das geänderte Dict oder None bei Abbruch."""

    BINDINGS = [("escape", "dismiss(None)", t("Abbrechen"))]

    def __init__(self, settings: dict) -> None:
        super().__init__()
        self._settings = settings

    def compose(self) -> ComposeResult:
        with Vertical(id="settings"):
            yield Label(t("Einstellungen"), id="settings-title")
            with Horizontal():
                yield Label(t("Autoplay (beim Start sofort abspielen)"))
                yield Switch(self._settings["autoplay"], id="set-autoplay")
            with Horizontal():
                yield Label(t("Cover neben dem Titel anzeigen"))
                yield Switch(self._settings["show_cover"], id="set-cover")
            with Horizontal():
                yield Label(t("Songtext anzeigen"))
                yield Switch(self._settings["show_lyrics"], id="set-lyrics")
            # Ohne Application-ID wird ohnehin nichts an Discord gesendet
            if "discord_presence" in self._settings:
                with Horizontal():
                    yield Label(t("Aktuellen Titel in Discord teilen"))
                    yield Switch(self._settings["discord_presence"], id="set-discord")
            with Horizontal():
                yield Label(t("Beim Start: Reihenfolge"))
                yield Select(
                    START_MODES,
                    value=self._settings["start_mode"],
                    allow_blank=False,
                    id="set-start-mode",
                )
            with Horizontal():
                yield Label(t("Sprache"))
                yield Select(
                    LANGUAGES,
                    value=self._settings["language"],
                    allow_blank=False,
                    id="set-language",
                )
            with Horizontal(id="settings-buttons"):
                yield Button(t("Abbrechen"), id="cancel")
                yield Button(t("Speichern"), id="save", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            result = {
                "autoplay": self.query_one("#set-autoplay", Switch).value,
                "show_cover": self.query_one("#set-cover", Switch).value,
                "show_lyrics": self.query_one("#set-lyrics", Switch).value,
                "start_mode": self.query_one("#set-start-mode", Select).value,
                "language": self.query_one("#set-language", Select).value,
            }
            if "discord_presence" in self._settings:
                result["discord_presence"] = self.query_one("#set-discord", Switch).value
            self.dismiss(result)
        else:
            self.dismiss(None)


class JellyfinClientLite(App):
    CSS = CSS
    TITLE = NAME
    AUTO_FOCUS = "#tracks"
    BINDINGS = [
        ("space", "toggle_pause", "Play/Pause"),
        ("n", "next_track", t("Nächster Titel")),
        ("p", "prev_track", t("Vorheriger Titel")),
        ("left", "seek_back", "-10s"),
        ("right", "seek_fwd", "+10s"),
        ("r", "reshuffle", t("Neu mischen")),
        # priority: sonst landet der "/" nach dem Fokuswechsel noch im Suchfeld
        Binding("slash", "search", t("Suche"), priority=True),
        ("a", "show_album", "Album"),
        ("l", "toggle_lyrics", t("Songtext")),
        ("c", "toggle_cover", "Cover"),
        ("s", "settings", t("Einstellungen")),
        Binding("escape", "close_search", t("Suche schließen"), show=False),
        ("plus", "vol_up", t("Lauter")),
        Binding("=", "vol_up", t("Lauter"), show=False),  # US-Layout
        ("-", "vol_down", t("Leiser")),
        ("q", "quit", t("Beenden")),
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
        self._halted = False  # nach zu vielen Ladefehlern angehalten
        self._crashes: list = []  # Zeitpunkte der letzten mpv-Abstürze
        self._loading = False  # Bibliothek wird gerade geladen
        self.config = config if config is not None else {}
        self._save_config = save_config
        self.all_tracks: List[Track] = []
        self.shown = self.queue  # was die Tabelle zeigt: die Queue oder Suchtreffer
        self._ordered_shuffle = True
        self.search_active = False  # Queue besteht nur aus Suchtreffern
        self._album_name: Optional[str] = None  # Tabelle zeigt gerade dieses Album
        self._search_timer = None
        self._extras_for = ""  # Titel, für den Cover/Songtext geladen (werden)
        self._covers: dict = {}  # Cover-ID -> fertig gerendertes Bild (None = keins)
        self._lyrics: list = []  # (Startzeit oder None, Text)
        self._lyric_line = -1

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
                    placeholder=t("Suche in Titel, Interpret, Album ..."),
                    id="search",
                    disabled=True,
                )
                search.border_title = t("Suche")
                search.border_subtitle = t("Enter = zur Liste, Esc = schließen")
                yield search
                table = TrackTable(id="tracks", cursor_type="row")
                table.border_subtitle = t("Klick oder Enter = abspielen")
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
                yield Static(t("Lade Bibliothek ..."), id="np-title", markup=False)
                yield Static("", id="np-sub", markup=False)
                yield SeekBar(ACCENT, id="np-progress")
                with Horizontal(id="controls"):
                    yield Control("⏮", "prev_track")
                    yield Control("⏸", "toggle_pause", id="btn-play")
                    yield Control("⏭", "next_track")
                    yield Control(t("⇄ Mischen"), "reshuffle")
                    yield Static(id="controls-spacer")
                    yield Control("−", "vol_down")
                    yield VolumeBar(ACCENT)
                    yield Control("+", "vol_up")
        yield Footer()

    def on_mount(self) -> None:
        table = self.main.query_one("#tracks", DataTable)
        table.add_columns(t("Titel"), t("Interpret"), "Album", t("Dauer"))
        self._update_title()
        self.main.query_one("#lyrics-panel").border_title = t("Songtext")
        if self.config.get("show_cover", True) and not cover_supported():
            self.notify(t("Für Cover fehlt Pillow:  pip install pillow"), severity="warning")
        self._apply_view()
        self.main.query_one("#np-title", Static).update(t("Lade Bibliothek ..."))
        self.main.query_one("#np-sub", Static).update(
            t("Kann bei großen Bibliotheken oder über VPN/Tunnel einen Moment dauern.")
        )
        self._start_loading()
        self.set_interval(1.0, self._tick)

    def _start_loading(self) -> None:
        """Lädt die Bibliothek im Hintergrund - nie zweimal gleichzeitig, auch
        wenn r mehrmals gedrückt wird, während der Server noch antwortet."""
        if self._loading:
            return
        self._loading = True
        self.run_worker(self._load_library, thread=True, exit_on_error=False)

    def _load_library(self) -> None:
        try:
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
        finally:
            self._loading = False

    def _on_library_error(self, exc: Exception) -> None:
        self.main.query_one("#np-title", Static).update(t("⚠ Bibliothek konnte nicht geladen werden"))
        self.main.query_one("#np-sub", Static).update(
            f"{type(exc).__name__}: {exc}  —  " + t("[r] zum erneuten Versuch")
        )

    def _on_library_loaded(self, tracks: List[Track]) -> None:
        if not tracks:
            self.main.query_one("#np-title", Static).update(
                t("Keine Titel in der Jellyfin-Bibliothek gefunden.")
            )
            return
        self.all_tracks = tracks
        self._reshuffle(tracks)
        if self.player is None and not self._start_player():
            return
        if self.config.get("autoplay", True):
            self._play_next()
        else:
            self.main.query_one("#np-title", Static).update(
                t("{n} Titel geladen - Leertaste oder Enter zum Abspielen", n=len(tracks))
            )
            self.main.query_one("#np-sub", Static).update("")

    def _start_player(self) -> bool:
        """Startet mpv. Klappt das nicht, steht der Grund in der Anzeige."""
        try:
            self.player = MPVPlayer(replaygain_args(self.config.get("replaygain")))
        except Exception as exc:
            self.main.query_one("#np-title", Static).update(t("⚠ mpv ließ sich nicht starten"))
            self.main.query_one("#np-sub", Static).update(
                f"{exc}  —  " + t("Leertaste für neuen Versuch")
            )
            return False
        self.player.set_http_headers(self.client.stream_headers())
        return True

    def _restart_player(self) -> None:
        """mpv ist abgestürzt oder wurde von außen beendet: neu starten und den
        aktuellen Titel noch einmal spielen - außer es passiert ständig."""
        code, log_path = self.player.exit_code, self.player.LOG_PATH
        self.player.shutdown()
        self.player = None
        now = time.monotonic()
        self._crashes = [c for c in self._crashes if now - c < 60] + [now]
        if len(self._crashes) > MAX_RESTARTS:
            self.main.query_one("#np-title", Static).update(t("⚠ mpv stürzt immer wieder ab"))
            self.main.query_one("#np-sub", Static).update(
                t("Details stehen in {path}  —  Leertaste für neuen Versuch", path=log_path)
            )
            return
        self.notify(
            t("mpv wurde unerwartet beendet (Exit-Code {code}) und neu gestartet.", code=code),
            severity="warning",
        )
        if self._start_player() and self.current_index >= 0 and not self._halted:
            self._play_track(self.current_index)

    def _on_library_updated(self, tracks: List[Track]) -> None:
        """Bibliothek hat sich auf dem Server geändert: neue Liste übernehmen,
        ohne den laufenden Titel zu unterbrechen."""
        if not tracks:
            return
        self.all_tracks = tracks
        if self.current_index < 0:
            self._reshuffle(tracks)  # es läuft noch nichts (Autoplay aus)
            self.notify(t("Bibliothek aktualisiert: {n} Titel", n=len(tracks)))
            return
        current = self.queue[self.current_index]
        self._reshuffle(tracks, first=current)
        self.current_index = 0
        self._update_neighbors()
        self._show_current()
        self.notify(t("Bibliothek aktualisiert: {n} Titel", n=len(tracks)))

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
            self.queue = [first] + [track for track in self.queue if track.id != first.id]
        self.current_index = -1
        self.search_active = False
        self._album_name = None
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
        for track in tracks:
            table.add_row(
                Text(track.title, overflow="ellipsis"),
                Text(track.artist, overflow="ellipsis"),
                Text(track.album, overflow="ellipsis"),
                track.duration_str,
                key=track.id,
            )
        table.set_wanted_widths(
            [
                typical_width((track.title for track in tracks), t("Titel")),
                typical_width((track.artist for track in tracks), t("Interpret")),
                typical_width((track.album for track in tracks), "Album"),
                max(len(t("Dauer")), max((len(track.duration_str) for track in tracks), default=0)),
            ]
        )

    def _update_title(self) -> None:
        table = self.main.query_one(TrackTable)
        if self._album_name is not None:
            # Text statt str: der Rahmentitel würde str als Markup lesen
            table.border_title = Text(f"Album: {self._album_name} ({len(self.shown)})")
        elif self.shown is not self.queue:
            table.border_title = t("Suche: {n} Treffer", n=len(self.shown))
        elif self.search_active:
            table.border_title = t("Suchtreffer ({n})", n=len(self.queue))
        elif self._ordered_shuffle:
            table.border_title = t("Alle Titel (zufällige Reihenfolge)")
        else:
            table.border_title = t("Alle Titel (sortiert)")

    def _refresh_queue_panel(self) -> None:
        lv = self.main.query_one("#queue-list", ListView)
        lv.clear()
        upcoming = self.queue[self.current_index + 1 : self.current_index + 21]
        for track in upcoming:
            lv.append(ListItem(Label(f"{track.title} - {track.artist}", markup=False)))

    def _play_track(self, index: int) -> None:
        if not (0 <= index < len(self.queue)) or self.player is None:
            return
        self.current_index = index
        self._halted = False
        track = self.queue[index]
        self.player.load(self.client.stream_url(track.id))
        self._update_neighbors()
        self._show_current()

    def _update_neighbors(self) -> None:
        if self.player is None:
            return
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
        self._load_extras()

    def _play_next(self) -> None:
        if self.player is None:
            return
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
        if self.presence and self.config.get("discord_presence", True) and self.current_index >= 0:
            self.presence.update(self.queue[self.current_index], pos, dur, paused)

    def _tick(self) -> None:
        # Beim Abbau sind die Widgets schon weg, der Timer läuft aber noch kurz weiter
        if self.player is None or not self.is_running:
            return
        if not self.player.alive:
            self._restart_player()
            return
        self._refresh_status()
        self._sync_lyrics()
        if self._halted:
            return
        if self.player.failures >= MAX_FAILURES:
            self._halt()
            return
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

    def _halt(self) -> None:
        self.player.stop()
        self.player.failures = 0
        self._halted = True
        self._refresh_status()
        self.main.query_one("#np-title", Static).update(t("⚠ Server liefert keine Titel"))
        self.main.query_one("#np-sub", Static).update(
            t(
                "{n} Titel hintereinander ließen sich nicht laden. "
                "Wiedergabe angehalten - Leertaste für neuen Versuch.",
                n=MAX_FAILURES,
            )
        )

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
        if self.player is None:
            # mpv ließ sich nicht starten oder stürzte zu oft ab: neuer Versuch
            if not self.queue or not self._start_player():
                return
            self._crashes.clear()
            if self.current_index >= 0:
                self._play_track(self.current_index)
                return
        if self._halted and self.current_index >= 0:
            # Nach Ladefehlern angehalten: denselben Titel noch einmal versuchen
            self._play_track(self.current_index)
        elif self.current_index < 0 and self.queue:
            # Autoplay war aus: Leertaste startet bei der markierten Zeile
            table = self.main.query_one(TrackTable)
            self._play_track(table.cursor_row if self.shown is self.queue else 0)
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
        if not self.queue:
            # Laden ist vermutlich fehlgeschlagen -> erneut versuchen
            self._start_loading()
            return
        self._reshuffle(self.all_tracks if self.search_active else self.queue, shuffle=True)
        self._play_next()

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
            self.notify(t("Speichern fehlgeschlagen: {error}", error=exc), severity="error")
        self._apply_view()

    def action_toggle_lyrics(self) -> None:
        if not isinstance(self.screen, SettingsScreen):
            self._toggle_setting("show_lyrics")

    def action_toggle_cover(self) -> None:
        if isinstance(self.screen, SettingsScreen):
            return
        if not cover_supported():
            self.notify(t("Für Cover fehlt Pillow:  pip install pillow"), severity="warning")
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
        self._lyrics, self._lyric_line = [], -1
        if self.show_lyrics:
            self._show_lyrics_text(t("Lade Songtext ..."))
            self.run_worker(
                lambda: self._fetch_lyrics(track), thread=True, exit_on_error=False,
                group="lyrics", exclusive=True,
            )
        if self.show_cover:
            cover_id = track.cover_id
            if cover_id in self._covers:
                self.main.query_one(CoverArt).set_art(self._covers[cover_id])
            else:
                self.main.query_one(CoverArt).set_art(None)
                if not cover_id:
                    return
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

    def _on_lyrics(self, track: Track, lines: list) -> None:
        if not self._is_current(track):
            return
        self._lyrics, self._lyric_line = lines, -1
        if not lines:
            self._show_lyrics_text(t("Kein Songtext vorhanden."))
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
        und in den sichtbaren Bereich scrollen."""
        if not (self.show_lyrics and self._lyrics and self.player):
            return
        if any(start is None for start, _ in self._lyrics):
            return
        pos = self.player.position
        line = -1
        for i, (start, _) in enumerate(self._lyrics):
            if start <= pos:
                line = i
            else:
                break
        if line != self._lyric_line:
            self._lyric_line = line
            self._render_lyrics()
            panel = self.main.query_one("#lyrics-panel", VerticalScroll)
            panel.scroll_to(y=max(0, line - panel.size.height // 2), animate=False)

    def action_search(self) -> None:
        if not self.all_tracks or isinstance(self.screen, SettingsScreen):
            return
        search = self.main.query_one("#search", Input)
        if self.focused is search:
            raise SkipAction()  # "/" im Suchfeld ist ein normales Zeichen
        search.disabled = False
        search.add_class("visible")
        search.focus()

    def action_show_album(self) -> None:
        """Zeigt das Album des markierten Titels, in Titelreihenfolge. Spielen
        klappt wie bei Suchtreffern, Esc führt zurück zur ganzen Bibliothek."""
        if not self.shown or isinstance(self.screen, SettingsScreen):
            return
        table = self.main.query_one(TrackTable)
        track = self.shown[table.cursor_row]
        if not track.album:
            self.notify(t("Dieser Titel gehört zu keinem Album."))
            return
        # Eine noch ausstehende Suche überschriebe sonst gleich die Album-Ansicht
        if self._search_timer is not None:
            self._search_timer.stop()
        hits = album_tracks(self.all_tracks, track) or [track]
        self._album_name = track.album
        self._fill_table(hits)
        table.move_cursor(row=next(i for i, hit in enumerate(hits) if hit.id == track.id))

    def action_close_search(self) -> None:
        search = self.main.query_one("#search", Input)
        if not search.has_class("visible") and self._album_name is None:
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
            track
            for track in self.all_tracks
            if all(w in f"{track.title} {track.artist} {track.album}".casefold() for w in words)
        ]
        self._album_name = None
        self._fill_table(hits)

    def _end_search(self) -> None:
        """Suche beendet: Tabelle zeigt wieder die ganze Queue. Lief ein Treffer,
        wird die Bibliothek mit ihm als erstem Titel neu geordnet."""
        self._album_name = None
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
            "show_cover": self.config.get("show_cover", True),
            "show_lyrics": self.config.get("show_lyrics", True),
            "start_mode": self.config.get("start_mode", "shuffle"),
            "language": self.config.get("language") if self.config.get("language") in ("de", "en") else "auto",
        }
        if self.presence:
            current["discord_presence"] = self.config.get("discord_presence", True)

        def done(result: Optional[dict]) -> None:
            if result is None:
                return
            self.config.update(result)
            # Vor dem Speichern: die Anzeige verschwindet auch, wenn das fehlschlägt
            if self.presence and not self.config.get("discord_presence", True):
                self.presence.clear()
            try:
                if self._save_config:
                    self._save_config()
            except OSError as exc:
                self.notify(t("Speichern fehlgeschlagen: {error}", error=exc), severity="error")
                return
            self._apply_view()
            self.notify(t("Gespeichert (Autoplay, Reihenfolge und Sprache gelten ab dem nächsten Start)."))

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
