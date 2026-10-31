"""mpv-Optionen aus der Config."""

import pytest

from jfclite.player import replaygain_args


@pytest.mark.parametrize("mode", ["track", "album"])
def test_replaygain_modes(mode):
    assert replaygain_args(mode) == [f"--replaygain={mode}"]


@pytest.mark.parametrize("value", [None, "", "no", "--vo=null", 1, True, []])
def test_replaygain_everything_else_is_off(value):
    assert replaygain_args(value) == []
