"""Was Discord als Anzeige bekommt, ohne Discord und ohne echte Verbindung."""

import pytest

from jfclite.client import Track
from jfclite.discord import DiscordPresence


@pytest.fixture
def presence(monkeypatch):
    monkeypatch.setattr(DiscordPresence, "_run", lambda self: None)
    return DiscordPresence("123", "https://jelly.test/")


def activity_for(presence, monkeypatch, track):
    sent = []
    monkeypatch.setattr(presence, "_set", sent.append)
    presence.update(track, 0.0, 180.0, False)
    return sent[-1]


def test_cover_address_uses_the_cover_id(presence, monkeypatch):
    track = Track("1", "Song", "Artist", "Eins", 0, "ordner", 0, "1")
    assets = activity_for(presence, monkeypatch, track)["assets"]
    assert assets["large_image"].startswith("https://jelly.test/Items/1/Images/Primary?")
    assert assets["large_text"] == "Eins"


def test_same_track_is_sent_again_after_clear(presence, monkeypatch):
    # Einschalten nach dem Ausschalten: sonst gälte das Update als "keine Änderung"
    sent = []
    monkeypatch.setattr(presence, "_set", sent.append)
    track = Track("1", "Song", "Artist", "Eins", 0, "ordner", 0, "")
    presence.update(track, 0.0, 180.0, False)
    presence.clear()
    presence.update(track, 0.0, 180.0, False)
    assert [bool(activity) for activity in sent] == [True, False, True]


def test_track_without_cover_sends_no_image(presence, monkeypatch):
    track = Track("1", "Song", "Artist", "Eins", 0, "ordner", 0, "")
    assert "assets" not in activity_for(presence, monkeypatch, track)
