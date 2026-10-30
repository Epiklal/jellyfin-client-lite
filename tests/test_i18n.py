"""Sprachwahl - und dass jeder t("...")-Text im Code eine Übersetzung hat."""

import ast
import re
from pathlib import Path

import pytest

from jfclite import i18n

ROOT = Path(__file__).resolve().parent.parent
SOURCES = [ROOT / "run.py", *sorted((ROOT / "jfclite").glob("*.py"))]
ENV_VARS = ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE")


def source_texts():
    """Alle Texte, die im Code an t() gehen, als (Datei:Zeile, Text)."""
    found = []
    for path in SOURCES:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "t"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                found.append((f"{path.name}:{node.lineno}", node.args[0].value))
    return found


def placeholders(text):
    return set(re.findall(r"\{(\w+)\}", text))


@pytest.fixture(autouse=True)
def restore_language():
    before = i18n._language
    yield
    i18n._language = before


@pytest.fixture
def env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(i18n.sys, "platform", "linux")
    return monkeypatch


@pytest.mark.parametrize(
    "variables, expected",
    [
        ({}, "en"),
        ({"LANG": "de_DE.UTF-8"}, "de"),
        ({"LANG": "de_AT"}, "de"),
        ({"LANG": "en_US.UTF-8"}, "en"),
        ({"LANG": "fr_FR.UTF-8"}, "en"),
        ({"LANG": "C"}, "en"),
        ({"LANG": "C.UTF-8"}, "en"),
        ({"LANG": "POSIX"}, "en"),
        # LC_ALL hat Vorrang, ein leeres oder "C" zählt aber nicht
        ({"LC_ALL": "en_US.UTF-8", "LANG": "de_DE.UTF-8"}, "en"),
        ({"LC_ALL": "", "LANG": "de_DE.UTF-8"}, "de"),
        ({"LC_ALL": "C", "LANG": "de_DE.UTF-8"}, "de"),
        ({"LC_MESSAGES": "de_DE.UTF-8", "LANG": "en_US.UTF-8"}, "de"),
        ({"LANGUAGE": "de"}, "de"),
    ],
)
def test_detect(env, variables, expected):
    for name, value in variables.items():
        env.setenv(name, value)
    assert i18n._detect() == expected


def test_set_language_explicit(env):
    env.setenv("LANG", "de_DE.UTF-8")
    i18n.set_language("en")
    assert i18n._language == "en"
    i18n.set_language("de")
    assert i18n._language == "de"


@pytest.mark.parametrize("value", ["auto", None, "", "fr", 1])
def test_set_language_falls_back_to_system(env, value):
    env.setenv("LANG", "de_DE.UTF-8")
    i18n.set_language("en")
    i18n.set_language(value)
    assert i18n._language == "de"


def test_translate_and_format():
    text = "Fehler in {path}, Zeile {line}, Spalte {column}: {error}"
    i18n.set_language("en")
    assert i18n.t(text, path="x", line=1, column=2, error="e") == i18n.EN[text].format(
        path="x", line=1, column=2, error="e"
    )
    i18n.set_language("de")
    assert i18n.t(text, path="x", line=1, column=2, error="e") == "Fehler in x, Zeile 1, Spalte 2: e"


def test_unknown_text_stays_as_it_is():
    i18n.set_language("en")
    assert i18n.t("kein bekannter Text") == "kein bekannter Text"


def test_text_without_values_may_contain_braces():
    i18n.set_language("de")
    assert i18n.t("Jede Zeile außer der letzten vor } muss mit , enden, z.B.:").count("}") == 1


def test_source_scan_finds_texts():
    assert len(source_texts()) > 50


@pytest.mark.parametrize("where, text", source_texts())
def test_every_text_is_translated(where, text):
    assert text in i18n.EN, f"{where}: keine Übersetzung für {text!r}"


def test_translations_keep_their_placeholders():
    wrong = [de for de, en in i18n.EN.items() if placeholders(de) != placeholders(en)]
    assert wrong == []


def test_no_unused_translations():
    used = {text for _, text in source_texts()}
    assert sorted(set(i18n.EN) - used) == []
