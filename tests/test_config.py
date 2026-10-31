"""Config lesen, prüfen und schreiben."""

import json
import os
import stat
import sys

import pytest

import run
from jfclite.setup import _is_exposed_http, _normalize_url, _password_login, save_config


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """Lenkt beide Config-Orte in ein leeres Verzeichnis um."""
    xdg = tmp_path / "xdg" / "config.json"
    local = tmp_path / "local" / "config.json"
    xdg.parent.mkdir()
    local.parent.mkdir()
    monkeypatch.setattr(run, "XDG_CONFIG_PATH", xdg)
    monkeypatch.setattr(run, "LOCAL_CONFIG_PATH", local)
    return xdg, local


# --- find_config_path -------------------------------------------------------


def test_default_location_without_any_config(paths):
    assert run.find_config_path() == paths[0]


def test_xdg_wins_over_local(paths):
    xdg, local = paths
    xdg.write_text("{}")
    local.write_text("{}")
    assert run.find_config_path() == xdg


def test_local_config_is_found(paths):
    paths[1].write_text("{}")
    assert run.find_config_path() == paths[1]


def test_real_name_wins_over_txt(paths):
    local = paths[1]
    local.write_text("{}")
    local.with_name("config.json.txt").write_text("{}")
    assert run.find_config_path() == local


def test_hidden_txt_extension_is_found(paths):
    # Windows blendet bekannte Endungen aus
    txt = paths[1].with_name("config.json.txt")
    txt.write_text("{}")
    assert run.find_config_path() == txt


# --- load_config ------------------------------------------------------------


def test_missing_config_is_none(paths):
    assert run.load_config() is None


@pytest.mark.parametrize("content", [b"", b"  \n\t\r\n"])
def test_blank_config_is_none(paths, content):
    paths[0].write_bytes(content)
    assert run.load_config() is None


@pytest.mark.parametrize(
    "encoding", ["utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"]
)
def test_config_encodings(paths, encoding):
    # Notepad und PowerShell schreiben gern mit BOM oder UTF-16
    paths[0].write_bytes('{"username": "Jörg"}'.encode(encoding))
    assert run.load_config() == {"username": "Jörg"}


def test_broken_json_names_line_and_exits(paths, capsys):
    paths[0].write_text('{\n  "server_url": "x"\n  "username": "y"\n}')
    with pytest.raises(SystemExit) as exc:
        run.load_config()
    assert exc.value.code == 1
    assert "3" in capsys.readouterr().out


def test_backslash_in_path_gets_its_own_hint(paths, capsys):
    paths[0].write_text(r'{"mpv_path": "C:\mpv\mpv.exe"}')
    with pytest.raises(SystemExit):
        run.load_config()
    assert "C:/mpv/mpv.exe" in capsys.readouterr().out


def test_latin1_config_exits_with_hint(paths, capsys):
    paths[0].write_bytes('{"username": "Jörg"}'.encode("latin-1"))
    with pytest.raises(SystemExit) as exc:
        run.load_config()
    assert exc.value.code == 1
    assert "UTF-8" in capsys.readouterr().out


@pytest.mark.parametrize("content", ["[]", "1", '"text"', "null"])
def test_non_object_config_exits(paths, content):
    paths[0].write_text(content)
    with pytest.raises(SystemExit) as exc:
        run.load_config()
    assert exc.value.code == 1


# --- missing_settings -------------------------------------------------------


@pytest.mark.parametrize(
    "config, missing",
    [
        ({"server_url": "u", "username": "me", "password": "pw"}, []),
        # Jellyfin erlaubt Benutzer ohne Passwort
        ({"server_url": "u", "username": "me", "password": ""}, []),
        ({"server_url": "u", "access_token": "tok"}, []),
        ({"username": "me", "password": "pw"}, ["server_url"]),
        ({"server_url": "", "username": "me", "password": "pw"}, ["server_url"]),
        ({"server_url": 1, "username": "me", "password": "pw"}, ["server_url"]),
        ({"server_url": "u", "password": "pw"}, ["username"]),
        ({"server_url": "u", "username": 1, "password": "pw"}, ["username"]),
        ({"server_url": "u", "username": "me"}, ["password"]),
        ({"server_url": "u", "username": "me", "password": None}, ["password"]),
        ({"server_url": "u", "access_token": ""}, ["username", "password"]),
        ({}, ["server_url", "username", "password"]),
    ],
)
def test_missing_settings(config, missing):
    assert run.missing_settings(config) == missing


# --- save_config ------------------------------------------------------------


def test_save_config_round_trip(tmp_path):
    path = tmp_path / "sub" / "config.json"
    save_config(path, {"username": "Jörg"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"username": "Jörg"}
    assert "Jörg" in path.read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-Dateirechte")
def test_save_config_is_private(tmp_path):
    path = tmp_path / "config.json"
    save_config(path, {"password": "pw"})
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_save_config_leaves_no_temp_file(tmp_path):
    path = tmp_path / "config.json"
    save_config(path, {})
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]


def test_failed_save_keeps_the_old_config(tmp_path):
    path = tmp_path / "config.json"
    save_config(path, {"username": "old"})
    with pytest.raises(TypeError):
        save_config(path, {"username": object()})
    assert json.loads(path.read_text(encoding="utf-8")) == {"username": "old"}


# --- Einrichtung ------------------------------------------------------------


@pytest.mark.parametrize(
    "url, expected",
    [
        ("192.168.1.10:8096", "http://192.168.1.10:8096"),
        ("  jelly.test/  ", "http://jelly.test"),
        ("https://jelly.test/", "https://jelly.test"),
        ("", ""),
    ],
)
def test_normalize_url(url, expected):
    assert _normalize_url(url) == expected


@pytest.mark.parametrize(
    "url, exposed",
    [
        ("http://jelly.example.com:8096", True),
        ("http://8.8.8.8:8096", True),
        ("http://[2001:4860:4860::8888]:8096", True),
        ("https://jelly.example.com", False),
        ("http://192.168.1.10:8096", False),
        ("http://10.0.0.2", False),
        ("http://100.64.0.1:8096", False),  # Tailscale
        ("http://127.0.0.1:8096", False),
        ("http://[::1]:8096", False),
        ("http://localhost:8096", False),
        ("http://jellyfin:8096", False),
        ("http://nas.local:8096", False),
        ("http://nas.lan", False),
        ("http://fritz.nas.fritz.box:8096", False),
        ("http://nas.tail1234.ts.net:8096", False),
    ],
)
def test_is_exposed_http(url, exposed):
    assert _is_exposed_http(url) is exposed


def test_password_login_stores_the_token_not_the_password(monkeypatch):
    class Client:
        keep_token = False

        def login(self, username, password):
            self.credentials = (username, password)
            self.api_key = "new"

    answers = iter(["me"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    monkeypatch.setattr("getpass.getpass", lambda _="": "pw")
    client = Client()
    config = {"access_token": "old", "password": "older"}
    assert _password_login(client, config)
    assert client.credentials == ("me", "pw")
    assert config == {"username": "me", "access_token": "new"}
    assert client.keep_token  # sonst wäre der gespeicherte Token beim Beenden ungültig
    assert run.missing_settings({"server_url": "u", **config}) == []


# --- Beispiel-Config --------------------------------------------------------


def test_example_config_is_valid_and_complete():
    example = json.loads(run.EXAMPLE_PATH.read_text(encoding="utf-8"))
    assert run.missing_settings(example) == []
    assert {"language", "autoplay", "start_mode", "show_cover", "show_lyrics"} <= example.keys()
