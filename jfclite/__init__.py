"""jellyfin-client-lite: ein schlanker Terminal-Musikplayer für Jellyfin."""

import os
import sys
from pathlib import Path

NAME = "jellyfin-client-lite"
__version__ = "0.2.0"


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default) / NAME


if sys.platform == "win32":
    CONFIG_DIR = _xdg("APPDATA", "AppData/Roaming")
    CACHE_DIR = _xdg("LOCALAPPDATA", "AppData/Local") / "cache"
    STATE_DIR = _xdg("LOCALAPPDATA", "AppData/Local") / "logs"
else:  # Linux, macOS
    CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config")
    CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache")
    STATE_DIR = _xdg("XDG_STATE_HOME", ".local/state")  # Logs
