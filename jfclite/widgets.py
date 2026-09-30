"""Einzeilige Bedienelemente für die Maus: Knöpfe, Zeit- und Lautstärkebalken.

Bewusst schlicht statt Textuals animierter ProgressBar: es wird nur Text neu
gezeichnet, und nur dann, wenn sich der angezeigte Wert tatsächlich ändert.
"""

from __future__ import annotations

from typing import Iterable, Optional

from rich.cells import cell_len
from rich.text import Text
from textual import events
from textual.coordinate import Coordinate
from textual.geometry import Size
from textual.message import Message
from textual.widget import Widget
from textual.widgets import DataTable


def fmt_time(seconds: float) -> str:
    secs = int(seconds)
    return f"{secs // 60}:{secs % 60:02d}"


def typical_width(values: Iterable[str], label: str) -> int:
    """Breite, in die 90 % der Einträge ganz hineinpassen (mindestens so breit
    wie die Überschrift) - einzelne Ausreißer blähen die Spalte nicht auf."""
    lengths = sorted(cell_len(v) for v in values)
    if not lengths:
        return cell_len(label)
    return max(cell_len(label), lengths[int((len(lengths) - 1) * 0.9)])


class TrackTable(DataTable):
    """Titelliste, deren Spaltenbreiten sich nach typischen Eintragslängen und
    der Fensterbreite richten statt nach dem längsten Eintrag - ein einziges
    Album mit 165 Zeichen machte sonst jede Zeile riesig breit. Längere
    Einträge werden (als Text mit overflow="ellipsis") mit "…" gekürzt."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._wanted: list[int] = []

    def set_wanted_widths(self, widths: list[int]) -> None:
        """Wunschbreite je Spalte. Passt nicht alles hinein, werden alle außer
        der letzten anteilig schmaler."""
        self._wanted = widths
        self._fit_columns()

    def on_resize(self, event: events.Resize) -> None:
        self._fit_columns()

    def on_click(self, event: events.Click) -> None:
        """Ein Klick auf eine Zeile wählt sie sofort aus. Neuere Textual-
        Versionen melden die Auswahl erst beim zweiten Klick (der erste
        markiert nur); doppelte Meldungen ignoriert die App."""
        row = event.style.meta.get("row")
        if isinstance(row, int) and 0 <= row < self.row_count:
            row_key = self.coordinate_to_cell_key(Coordinate(row, 0)).row_key
            self.post_message(self.RowSelected(self, row, row_key))

    def _fit_columns(self) -> None:
        columns = self.ordered_columns
        if len(columns) != len(self._wanted) or not self.size.width:
            return
        *flexible, last = self._wanted
        avail = (
            self.content_region.width
            - self.styles.scrollbar_size_vertical
            - 2 * self.cell_padding * len(columns)
            - last
        )
        if sum(flexible) > avail:
            flexible = [max(3, avail * w // sum(flexible)) for w in flexible]
        for column, width in zip(columns, [*flexible, last]):
            column.auto_width = False
            column.width = width
        # DataTable kann Spaltenbreiten nicht nachträglich ändern (nur beim
        # Anlegen) - Scrollbereich und zwischengespeicherte Zeilen deshalb
        # selbst aktualisieren.
        self.virtual_size = Size(
            sum(column.get_render_width(self) for column in columns),
            self.virtual_size.height,
        )
        self._clear_caches()
        self.refresh()


class Control(Widget):
    """Klick-Knopf in einer Zeile. Nicht fokussierbar, damit Pfeiltasten usw.
    nach einem Klick weiter bei der Titelliste landen."""

    DEFAULT_CSS = """
    Control {
        width: auto;
        height: 1;
        padding: 0 1;
    }
    """

    def __init__(self, label: str, action: str, *, id: Optional[str] = None):
        super().__init__(id=id)
        self.label = label
        self._action = action

    def render(self) -> str:
        return self.label

    def set_label(self, label: str) -> None:
        if label != self.label:
            self.label = label
            self.refresh(layout=True)

    async def on_click(self, event: events.Click) -> None:
        event.stop()
        await self.app.run_action(self._action)


class SeekBar(Widget):
    """Fortschritt mit Zeitangabe; ein Klick springt an diese Stelle."""

    DEFAULT_CSS = """
    SeekBar {
        height: 1;
    }
    """

    class Seek(Message):
        def __init__(self, seconds: float) -> None:
            super().__init__()
            self.seconds = seconds

    def __init__(self, color: str, *, id: Optional[str] = None):
        super().__init__(id=id)
        self._color = color
        self._pos = 0.0
        self._dur = 0.0

    def set_progress(self, pos: float, dur: float) -> None:
        changed = (int(pos), int(dur)) != (int(self._pos), int(self._dur))
        self._pos, self._dur = pos, dur
        if changed:
            self.refresh()

    def _parts(self) -> tuple[str, str, int]:
        """Linke Zeit, rechte Zeit, Breite des Balkens dazwischen."""
        left, right = fmt_time(self._pos), fmt_time(self._dur)
        return left, right, max(0, self.size.width - len(left) - len(right) - 2)

    def render(self) -> Text:
        left, right, width = self._parts()
        filled = min(width, round(width * self._pos / self._dur)) if self._dur else 0
        return Text.assemble(
            left,
            " ",
            ("━" * filled, self._color),
            ("─" * (width - filled), "dim"),
            " ",
            right,
        )

    def on_click(self, event: events.Click) -> None:
        left, _, width = self._parts()
        if not self._dur or not width:
            return
        fraction = (event.x - len(left) - 1) / width
        self.post_message(self.Seek(max(0.0, min(1.0, fraction)) * self._dur))


class VolumeBar(Widget):
    """Lautstärke als Balken: Klick setzt sie, das Mausrad ändert sie in
    5er-Schritten."""

    BAR_WIDTH = 10
    DEFAULT_CSS = """
    VolumeBar {
        width: 19;
        height: 1;
    }
    """

    class Changed(Message):
        def __init__(self, volume: int) -> None:
            super().__init__()
            self.volume = volume

    def __init__(self, color: str, *, id: Optional[str] = None):
        super().__init__(id=id)
        self._color = color
        self._volume = 100
        self._before_mute = 100

    def set_volume(self, volume: float) -> None:
        volume = round(volume)
        if volume != self._volume:
            self._volume = volume
            self.refresh()

    def render(self) -> Text:
        icon = "🔇" if self._volume == 0 else "🔊"  # 2 Zellen breit
        filled = min(self.BAR_WIDTH, round(self.BAR_WIDTH * self._volume / 100))
        return Text.assemble(
            icon,
            " ",
            ("━" * filled, self._color),
            ("─" * (self.BAR_WIDTH - filled), "dim"),
            f" {self._volume:3d}%",
        )

    def on_click(self, event: events.Click) -> None:
        cell = event.x - 3  # Symbol + Leerzeichen davor
        if 0 <= cell < self.BAR_WIDTH:
            self.post_message(self.Changed((cell + 1) * 100 // self.BAR_WIDTH))
        elif cell < 0:
            # Klick aufs Symbol: stumm schalten bzw. alte Lautstärke zurück
            if self._volume:
                self._before_mute = self._volume
                self.post_message(self.Changed(0))
            else:
                self.post_message(self.Changed(self._before_mute))

    async def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.stop()
        await self.app.run_action("vol_up")

    async def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.stop()
        await self.app.run_action("vol_down")
