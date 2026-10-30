"""Anmeldung, Token-Umgang, Cache und Sortierung - ohne Netzwerk: ein Fake ersetzt
die requests-Session des Clients."""

import json

import pytest
import requests

from jfclite import client as client_module
from jfclite.client import (
    JellyfinClient,
    Track,
    _auth_header,
    album_tracks,
    sorted_tracks,
)

SERVER = "http://jelly.test:8096"
LOGIN_RESPONSE = {"AccessToken": "secret-token", "User": {"Id": "user-1"}}


class FakeResponse:
    def __init__(self, status_code=200, data=None, content=b""):
        self.status_code = status_code
        self._data = data
        self.content = content

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeSession:
    """Antwortet je Pfad aus `responses` und merkt sich die Aufrufe."""

    def __init__(self, responses=None):
        self.headers = {}
        self.responses = responses or {}
        self.calls = []

    def _answer(self, method, url, kwargs):
        path = url[len(SERVER):]
        self.calls.append((method, path, kwargs, self.headers.get("Authorization")))
        answer = self.responses[path]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def get(self, url, **kwargs):
        return self._answer("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self._answer("POST", url, kwargs)


def make_client(responses=None):
    client = JellyfinClient(SERVER)
    session = FakeSession(responses)
    session.headers["Authorization"] = _auth_header()
    client._session = session
    return client, session


def track(id="1", title="Song", artist="Artist", album="Album", album_id="a1", track_no=0):
    return Track(id, title, artist, album, 0, album_id, track_no)


# --- Header und Adressen ----------------------------------------------------


def test_auth_header_without_token():
    header = _auth_header()
    assert header.startswith('MediaBrowser Client="')
    assert "Token=" not in header


def test_auth_header_with_token():
    assert _auth_header("abc").endswith(', Token="abc"')


def test_trailing_slash_is_removed_from_server_url():
    assert JellyfinClient(SERVER + "/").server_url == SERVER


def test_stream_url_contains_no_token():
    client, _ = make_client({"/Users/AuthenticateByName": FakeResponse(data=LOGIN_RESPONSE)})
    client.login("me", "pw")
    assert "secret-token" not in client.stream_url("item")
    assert client.stream_url("item") == f"{SERVER}/Audio/item/stream?static=true"


def test_stream_headers_carry_the_token():
    client, _ = make_client({"/Users/AuthenticateByName": FakeResponse(data=LOGIN_RESPONSE)})
    client.login("me", "pw")
    assert client.stream_headers() == [f"Authorization: {_auth_header('secret-token')}"]


# --- Anmeldung mit Passwort -------------------------------------------------


def test_login_sets_token_and_header():
    client, session = make_client({"/Users/AuthenticateByName": FakeResponse(data=LOGIN_RESPONSE)})
    client.login("me", "pw")
    assert client.api_key == "secret-token"
    assert client.user_id == "user-1"
    assert client.keep_token is False
    assert session.headers["Authorization"] == _auth_header("secret-token")
    assert session.calls[0][2]["json"] == {"Username": "me", "Pw": "pw"}


def test_login_wrong_password():
    client, _ = make_client({"/Users/AuthenticateByName": FakeResponse(401)})
    with pytest.raises(RuntimeError):
        client.login("me", "wrong")
    assert client.api_key is None


def test_login_server_error_is_not_reported_as_wrong_password():
    client, _ = make_client({"/Users/AuthenticateByName": FakeResponse(500)})
    with pytest.raises(requests.HTTPError):
        client.login("me", "pw")


def test_unauthenticated_requests_fail_clearly():
    client, _ = make_client()
    with pytest.raises(RuntimeError):
        client.resolve_user_id()


# --- Gespeicherter Token ----------------------------------------------------


def test_login_with_token_keeps_the_token():
    client, session = make_client({"/Users/Me": FakeResponse(data={"Id": "user-1"})})
    client.login_with_token("saved")
    assert client.api_key == "saved"
    assert client.user_id == "user-1"
    assert client.keep_token is True
    assert session.calls[0][3] == _auth_header("saved")


def test_login_with_revoked_token_drops_it_again():
    client, session = make_client({"/Users/Me": FakeResponse(401)})
    with pytest.raises(RuntimeError):
        client.login_with_token("revoked")
    assert client.api_key is None
    assert client.keep_token is False
    assert "revoked" not in session.headers["Authorization"]


# --- Abmelden ---------------------------------------------------------------


def test_logout_revokes_password_token():
    client, session = make_client(
        {
            "/Users/AuthenticateByName": FakeResponse(data=LOGIN_RESPONSE),
            "/Sessions/Logout": FakeResponse(204),
        }
    )
    client.login("me", "pw")
    client.logout()
    assert ("POST", "/Sessions/Logout") in [c[:2] for c in session.calls]
    assert client.api_key is None


def test_logout_keeps_saved_token():
    client, session = make_client({"/Users/Me": FakeResponse(data={"Id": "user-1"})})
    client.login_with_token("saved")
    client.logout()
    assert "/Sessions/Logout" not in [c[1] for c in session.calls]
    assert client.api_key == "saved"


def test_logout_without_login_does_nothing():
    client, session = make_client()
    client.logout()
    assert session.calls == []


def test_logout_survives_network_errors():
    client, _ = make_client(
        {
            "/Users/AuthenticateByName": FakeResponse(data=LOGIN_RESPONSE),
            "/Sessions/Logout": requests.ConnectionError(),
        }
    )
    client.login("me", "pw")
    client.logout()
    assert client.api_key is None


# --- Quick Connect ----------------------------------------------------------


def test_quick_connect_start():
    client, _ = make_client(
        {"/QuickConnect/Initiate": FakeResponse(data={"Code": "123456", "Secret": "s3"})}
    )
    assert client.quick_connect_start() == ("123456", "s3")


@pytest.mark.parametrize("status", [400, 401, 403])
def test_quick_connect_disabled_on_server(status):
    client, _ = make_client({"/QuickConnect/Initiate": FakeResponse(status)})
    with pytest.raises(RuntimeError):
        client.quick_connect_start()


@pytest.mark.parametrize("authenticated", [True, False])
def test_quick_connect_authorized(authenticated):
    client, session = make_client(
        {"/QuickConnect/Connect": FakeResponse(data={"Authenticated": authenticated})}
    )
    assert client.quick_connect_authorized("s3") is authenticated
    assert session.calls[0][2]["params"] == {"secret": "s3"}


def test_quick_connect_login_keeps_the_token():
    client, _ = make_client(
        {"/Users/AuthenticateWithQuickConnect": FakeResponse(data=LOGIN_RESPONSE)}
    )
    client.quick_connect_login("s3")
    assert client.api_key == "secret-token"
    assert client.keep_token is True


# --- Tags aus Dateien -------------------------------------------------------


def test_track_strips_terminal_escapes():
    t = Track("1", "Ti\x1b]52;c;AAAA\x07tle", "Art\x9bist", " Al\x00bum ", 0)
    assert "\x1b" not in t.title and "\x07" not in t.title
    assert t.artist == "Art ist"
    assert t.album == "Al bum"


def test_track_strips_bidi_controls():
    assert Track("1", "a‮b⁦c", "", "", 0).title == "a b c"


def test_track_keeps_normal_unicode():
    assert Track("1", "Grüße 日本語 🎵", "", "", 0).title == "Grüße 日本語 🎵"


def test_lyrics_are_sanitized_and_converted_to_seconds():
    client, _ = make_client(
        {
            "/Audio/1/Lyrics": FakeResponse(
                data={"Lyrics": [{"Start": 25_000_000, "Text": "la\x1b[31m"}, {"Text": None}]}
            )
        }
    )
    assert client.fetch_lyrics("1") == [(2.5, "la [31m"), (None, "")]


@pytest.mark.parametrize("status", [400, 404])
def test_missing_lyrics_are_empty(status):
    client, _ = make_client({"/Audio/1/Lyrics": FakeResponse(status)})
    assert client.fetch_lyrics("1") == []


def test_missing_cover_is_none():
    client, _ = make_client({"/Items/1/Images/Primary": FakeResponse(404)})
    assert client.fetch_cover("1") is None


def test_duration_str():
    assert Track("1", "", "", "", 65 * 10_000_000).duration_str == "1:05"


# --- Bibliothek und Cache ---------------------------------------------------


@pytest.fixture
def cache_path(tmp_path, monkeypatch):
    path = tmp_path / "cache" / "library.json"
    monkeypatch.setattr(client_module, "CACHE_PATH", path)
    return path


def logged_in_client(responses=None):
    client, session = make_client(responses)
    client.api_key = "token"
    client.user_id = "user-1"
    return client, session


def test_fetch_all_tracks_reads_every_page(cache_path):
    pages = iter(
        [
            {"Items": [{"Id": str(i), "Name": "x"} for i in range(500)], "TotalRecordCount": 501},
            {"Items": [{"Id": "500", "Name": "x"}], "TotalRecordCount": 501},
        ]
    )

    class Paged(FakeSession):
        def get(self, url, **kwargs):
            self.calls.append(kwargs["params"]["StartIndex"])
            return FakeResponse(data=next(pages))

    client, _ = logged_in_client()
    client._session = Paged()
    assert len(client.fetch_all_tracks()) == 501
    assert client._session.calls == [0, 500]


def test_fetch_all_tracks_maps_fields(cache_path):
    item = {
        "Id": "1",
        "Name": "Song",
        "Artists": ["Featured"],
        "Album": "Album",
        "AlbumId": "a1",
        "RunTimeTicks": 10_000_000,
        "ParentIndexNumber": 2,
        "IndexNumber": 3,
    }
    client, _ = logged_in_client(
        {"/Users/user-1/Items": FakeResponse(data={"Items": [item], "TotalRecordCount": 1})}
    )
    assert client.fetch_all_tracks() == [Track("1", "Song", "Featured", "Album", 10_000_000, "a1", 2003)]


def test_album_artist_wins_over_track_artists(cache_path):
    item = {"Id": "1", "AlbumArtist": "Band", "Artists": ["Featured"]}
    client, _ = logged_in_client(
        {"/Users/user-1/Items": FakeResponse(data={"Items": [item], "TotalRecordCount": 1})}
    )
    assert client.fetch_all_tracks()[0].artist == "Band"


def fetched(*items):
    client, _ = logged_in_client(
        {"/Users/user-1/Items": FakeResponse(data={"Items": list(items), "TotalRecordCount": len(items)})}
    )
    return {t.id: t.cover_id for t in client.fetch_all_tracks()}


def test_own_image_wins_over_album_image(cache_path):
    item = {
        "Id": "1", "Album": "A", "AlbumId": "a1",
        "ImageTags": {"Primary": "tag"}, "AlbumPrimaryImageTag": "albumtag",
    }
    assert fetched(item) == {"1": "1"}


def test_track_without_own_image_uses_the_album_image(cache_path):
    item = {"Id": "1", "Album": "A", "AlbumId": "a1", "AlbumPrimaryImageTag": "albumtag"}
    assert fetched(item) == {"1": "a1"}


def test_track_without_any_image_has_no_cover(cache_path):
    assert fetched({"Id": "1", "Album": "A", "AlbumId": "a1"}) == {"1": ""}
    assert fetched({"Id": "1"}) == {"1": ""}


def test_loose_tracks_never_show_the_image_of_a_shared_album_id(cache_path):
    # Lose Titel im selben Ordner: gleiche AlbumId, aber verschiedene Album-Namen
    shared = {"AlbumId": "ordner", "AlbumPrimaryImageTag": "fremd"}
    items = [
        {"Id": "1", "Album": "Eins", "ImageTags": {"Primary": "tag"}, **shared},
        {"Id": "2", "Album": "Zwei", **shared},
        {"Id": "3", **shared},
    ]
    assert fetched(*items) == {"1": "1", "2": "", "3": ""}


def test_cover_id_survives_the_cache(cache_path):
    client, _ = logged_in_client()
    client._save_cache([Track("1", "", "", "", 0, "a1", 0, "1")], "2026-01-01T00:00:00Z")
    assert client.load_cache()[0][0].cover_id == "1"


def test_cache_round_trip(cache_path):
    client, _ = logged_in_client()
    tracks = [track(id="1", track_no=1002), track(id="2", title="Zwei")]
    client._save_cache(tracks, "2026-01-01T00:00:00Z")
    assert client.load_cache() == (tracks, "2026-01-01T00:00:00Z")


def test_cache_holds_no_token(cache_path):
    client, _ = logged_in_client()
    client._save_cache([track()], "2026-01-01T00:00:00Z")
    assert client.api_key not in cache_path.read_text(encoding="utf-8")


def test_cache_of_other_version_is_ignored(cache_path, monkeypatch):
    client, _ = logged_in_client()
    client._save_cache([track()], "2026-01-01T00:00:00Z")
    monkeypatch.setattr(client_module, "CACHE_VERSION", client_module.CACHE_VERSION + 1)
    assert client.load_cache() is None


def test_cache_of_other_user_is_ignored(cache_path):
    client, _ = logged_in_client()
    client._save_cache([track()], "2026-01-01T00:00:00Z")
    client.user_id = "user-2"
    assert client.load_cache() is None


def test_cache_of_other_server_is_ignored(cache_path):
    client, _ = logged_in_client()
    client._save_cache([track()], "2026-01-01T00:00:00Z")
    client.server_url = "http://other.test"
    assert client.load_cache() is None


@pytest.mark.parametrize("content", ["", "not json", "{}", '{"version": 4}', "[1, 2]"])
def test_broken_cache_is_ignored(cache_path, content):
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(content, encoding="utf-8")
    client, _ = logged_in_client()
    # Eine Liste statt eines Objekts darf den Start genauso wenig aufhalten
    assert client.load_cache() is None


def test_missing_cache_is_ignored(cache_path):
    client, _ = logged_in_client()
    assert client.load_cache() is None


# --- Album und Sortierung ---------------------------------------------------


def test_album_tracks_in_track_order():
    second = track(id="2", title="A", track_no=2)
    first = track(id="1", title="B", track_no=1)
    other = track(id="3", album_id="a2", track_no=1)
    assert album_tracks([second, other, first], first) == [first, second]


def test_album_tracks_multi_disc_order():
    cd2 = track(id="2", track_no=2001)
    cd1 = track(id="1", track_no=1009)
    assert album_tracks([cd2, cd1], cd2) == [cd1, cd2]


def test_album_tracks_without_id_tells_same_named_albums_apart():
    mine = track(id="1", album="Greatest Hits", artist="A", album_id="")
    other = track(id="2", album="Greatest Hits", artist="B", album_id="")
    assert album_tracks([mine, other], mine) == [mine]


def test_album_tracks_with_shared_id_keeps_albums_apart():
    # Lose Dateien in einem Ordner: Jellyfin vergibt für alle dieselbe AlbumId
    mine = track(id="1", album="Eins", album_id="ordner")
    other = track(id="2", album="Zwei", album_id="ordner")
    assert album_tracks([mine, other], mine) == [mine]


def test_sorted_tracks_order():
    b = track(id="b", artist="beta", album="x", track_no=1)
    a2 = track(id="a2", artist="Alpha", album="x", title="A", track_no=2)
    a1 = track(id="a1", artist="alpha", album="x", title="Z", track_no=1)
    assert sorted_tracks([b, a2, a1]) == [a1, a2, b]
