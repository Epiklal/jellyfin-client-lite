"""Die Oberfläche ohne Terminal, mpv und Server: Album-Sprung, Suche, Einstellungen."""

import asyncio

import pytest
from textual.widgets import Button, Input, Select, Switch

from jfclite import app as app_module
from jfclite.app import JellyfinClientLite, SettingsScreen, TrackTable
from jfclite.client import Track


def track(id, title, album, track_no=0, artist="Artist"):
    return Track(id, title, artist, album, 1_800_000_000, f"album-{album}", track_no)


TRACKS = [
    track("a2", "Zwei", "Album A", 2),
    track("b1", "Anderes", "[Live] Album B", 1),
    track("a1", "Eins", "Album A", 1),
    track("x1", "Einzeln", ""),
    track("a3", "Drei", "Album A", 3),
]


class FakePlayer:
    LOG_PATH = "mpv.log"
    alive = True
    exit_code = None
    failures = 0
    is_idle = False
    is_paused = False
    position = 0.0
    duration = 0.0
    volume = 100.0

    def __init__(self, *args, **kwargs):
        self.loaded = []

    def load(self, url, *args, **kwargs):
        self.loaded.append(url)

    def playlist_step(self):
        return 0

    def get_property(self, *args, **kwargs):
        return None

    def __getattr__(self, name):
        # Restliche Steuerbefehle (Pause, Lautstärke, Beenden ...) tun hier nichts
        return lambda *args, **kwargs: None


class FakePresence:
    def __init__(self):
        self.updates = []
        self.cleared = 0

    def update(self, track, pos, dur, paused):
        self.updates.append(track.id)

    def clear(self):
        self.cleared += 1

    def close(self):
        pass


class FakeClient:
    def __init__(self, tracks):
        self.tracks = tracks
        self.cover_requests = []

    def load_cache(self):
        return None

    def fetch_all_tracks(self):
        return self.tracks

    def library_changed(self, count, fetched_at):
        return False

    def stream_headers(self):
        return {}

    def stream_url(self, track_id):
        return f"http://jelly.test/{track_id}"

    def fetch_cover(self, item_id):
        self.cover_requests.append(item_id)
        return None

    def fetch_lyrics(self, *args, **kwargs):
        return []


@pytest.fixture(autouse=True)
def fake_player(monkeypatch):
    monkeypatch.setattr(app_module, "MPVPlayer", FakePlayer)


def run_app(scenario, tracks=TRACKS, config=None, save_config=None, size=(80, 24), presence=None):
    """Startet die App headless, wartet auf die Bibliothek und führt `scenario` aus."""

    async def main():
        settings = {"autoplay": False, "start_mode": "sorted", **(config or {})}
        app = JellyfinClientLite(FakeClient(tracks), presence, settings, save_config)
        async with app.run_test(size=size) as pilot:
            for _ in range(200):
                if app.all_tracks:
                    break
                await pilot.pause(0.01)
            assert app.all_tracks, "Bibliothek wurde nicht geladen"
            await pilot.pause()
            await scenario(app, pilot)

    asyncio.run(main())


def table_of(app):
    return app.main.query_one(TrackTable)


async def select_row(app, pilot, track_id):
    table = table_of(app)
    table.move_cursor(row=[t.id for t in app.shown].index(track_id))
    await pilot.pause()


def shown_ids(app):
    return [t.id for t in app.shown]


def test_album_key_shows_album_in_track_order():
    async def scenario(app, pilot):
        await select_row(app, pilot, "a3")
        await pilot.press("a")
        await pilot.pause()
        assert shown_ids(app) == ["a1", "a2", "a3"]
        # Der Cursor bleibt auf dem Titel, von dem aus gesprungen wurde
        assert app.shown[table_of(app).cursor_row].id == "a3"
        assert "Album A (3)" in str(table_of(app).border_title)

    run_app(scenario)


def test_album_name_with_brackets_stays_in_the_title():
    # Eckige Klammern dürfen nicht als Markup verschluckt werden
    async def scenario(app, pilot):
        await select_row(app, pilot, "b1")
        await pilot.press("a")
        await pilot.pause()
        assert shown_ids(app) == ["b1"]
        assert "[Live] Album B (1)" in str(table_of(app).border_title)

    run_app(scenario)


def test_track_without_album_stays_in_the_library():
    async def scenario(app, pilot):
        before = shown_ids(app)
        await select_row(app, pilot, "x1")
        await pilot.press("a")
        await pilot.pause()
        assert shown_ids(app) == before
        assert app._album_name is None

    run_app(scenario)


def test_escape_returns_from_album_to_library():
    async def scenario(app, pilot):
        before = shown_ids(app)
        await select_row(app, pilot, "a3")
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert shown_ids(app) == before
        assert app._album_name is None

    run_app(scenario)


def test_playing_from_album_keeps_the_track_after_escape():
    async def scenario(app, pilot):
        await select_row(app, pilot, "a2")
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.search_active
        assert app.queue[app.current_index].id == "a2"
        assert app.player.loaded[-1].endswith("a2")

        await pilot.press("escape")
        await pilot.pause()
        assert not app.search_active
        assert sorted(shown_ids(app)) == sorted(t.id for t in TRACKS)
        assert app.queue[app.current_index].id == "a2"
        assert len(app.player.loaded) == 1  # läuft einfach weiter

    run_app(scenario)


def test_cover_is_requested_for_the_cover_id_only():
    tracks = [
        Track("1", "Mit Bild", "Artist", "Eins", 1_800_000_000, "ordner", 0, "1"),
        Track("2", "Ohne Bild", "Artist", "Zwei", 1_800_000_000, "ordner", 0, ""),
    ]

    async def scenario(app, pilot):
        await select_row(app, pilot, "1")
        await pilot.press("enter")
        await pilot.pause(0.1)
        await select_row(app, pilot, "2")
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert app.queue[app.current_index].id == "2"
        assert app.client.cover_requests == ["1"]

    run_app(scenario, tracks=tracks, config={"show_cover": True})


def test_search_filters_and_escape_ends_it():
    async def scenario(app, pilot):
        await pilot.press("slash")
        await pilot.pause()
        search = app.main.query_one("#search", Input)
        assert app.focused is search
        await pilot.press(*"eins")
        await pilot.pause(0.3)
        assert shown_ids(app) == ["a1"]
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.shown) == len(TRACKS)

    run_app(scenario)


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
@pytest.mark.parametrize("presence", [None, FakePresence()])
def test_settings_dialog_fits_the_terminal(size, presence):
    async def scenario(app, pilot):
        await pilot.press("s")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SettingsScreen)
        for widget in screen.query("Switch, Select, Button"):
            region = widget.region
            assert region.height > 0, widget.id
            assert region.y >= 0 and region.bottom <= size[1], widget.id
            assert region.x >= 0 and region.right <= size[0], widget.id

    run_app(scenario, size=size, presence=presence)


@pytest.mark.parametrize("label", ["Sortiert (Interpret, Album)", "Sorted (artist, album)"])
@pytest.mark.parametrize("presence", [None, FakePresence()])
def test_settings_dialog_needs_no_scrolling_at_80x24(monkeypatch, label, presence):
    # Ein umbrochener Eintrag in der Auswahl schob früher den unteren Rand aus dem Bild
    monkeypatch.setattr(app_module, "START_MODES", [(label, "sorted"), ("Shuffle", "shuffle")])

    async def scenario(app, pilot):
        await pilot.press("s")
        await pilot.pause()
        screen = app.screen
        assert screen.max_scroll_y == 0
        assert screen.max_scroll_x == 0
        assert screen.query_one("#settings").region.bottom <= 24

    run_app(scenario, presence=presence)


def test_settings_save_updates_and_stores_the_config():
    saved = []

    async def scenario(app, pilot):
        await pilot.press("s")
        await pilot.pause()
        screen = app.screen
        screen.query_one("#set-cover", Switch).value = False
        screen.query_one("#set-language", Select).value = "en"
        screen.query_one("#save", Button).press()
        await pilot.pause()
        assert not isinstance(app.screen, SettingsScreen)
        assert app.config["show_cover"] is False
        assert app.config["language"] == "en"
        assert saved == [True]

    run_app(scenario, save_config=lambda: saved.append(True))


def test_settings_cancel_changes_nothing():
    saved = []

    async def scenario(app, pilot):
        await pilot.press("s")
        await pilot.pause()
        app.screen.query_one("#set-cover", Switch).value = False
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, SettingsScreen)
        assert "show_cover" not in app.config
        assert saved == []

    run_app(scenario, save_config=lambda: saved.append(True))


def test_discord_switch_needs_an_application_id():
    async def scenario(app, pilot):
        await pilot.press("s")
        await pilot.pause()
        assert not app.screen.query("#set-discord")
        app.screen.query_one("#save", Button).press()
        await pilot.pause()
        assert "discord_presence" not in app.config

    run_app(scenario)


def test_turning_discord_off_and_on_again():
    presence = FakePresence()
    saved = []

    async def set_sharing(app, pilot, value):
        await pilot.press("s")
        await pilot.pause()
        app.screen.query_one("#set-discord", Switch).value = value
        app.screen.query_one("#save", Button).press()
        await pilot.pause()

    async def scenario(app, pilot):
        app.current_index = 0
        app._refresh_status()
        assert presence.updates and presence.cleared == 0

        await set_sharing(app, pilot, False)
        assert app.config["discord_presence"] is False
        assert saved == [True]
        assert presence.cleared == 1
        presence.updates.clear()
        app._refresh_status()
        assert presence.updates == []

        await set_sharing(app, pilot, True)
        assert presence.cleared == 1
        app._refresh_status()
        assert presence.updates

    run_app(scenario, save_config=lambda: saved.append(True), presence=presence)


def test_discord_stays_silent_if_turned_off_in_the_config():
    presence = FakePresence()

    async def scenario(app, pilot):
        app.current_index = 0
        app._refresh_status()
        assert presence.updates == []
        await pilot.press("s")
        await pilot.pause()
        assert app.screen.query_one("#set-discord", Switch).value is False

    run_app(scenario, config={"discord_presence": False}, presence=presence)


def test_turning_discord_off_works_even_if_saving_fails():
    presence = FakePresence()

    def fail():
        raise OSError("read-only")

    async def scenario(app, pilot):
        await pilot.press("s")
        await pilot.pause()
        app.screen.query_one("#set-discord", Switch).value = False
        app.screen.query_one("#save", Button).press()
        await pilot.pause()
        assert presence.cleared == 1

    run_app(scenario, save_config=fail, presence=presence)


def test_failed_save_does_not_crash():
    def fail():
        raise OSError("read-only")

    async def scenario(app, pilot):
        await pilot.press("s")
        await pilot.pause()
        app.screen.query_one("#save", Button).press()
        await pilot.pause()
        assert not isinstance(app.screen, SettingsScreen)

    run_app(scenario, save_config=fail)
