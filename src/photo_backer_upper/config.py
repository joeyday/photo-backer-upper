"""Settings come from environment variables, falling back to a config file.

launchd jobs don't read ~/.zshrc, so scheduled runs need somewhere else to find
settings. The config file (default ~/.config/photo-backer-upper/config) uses simple
KEY=VALUE lines, the same names as the environment variables. Real environment
variables win over the file.
"""

import os
from pathlib import Path


def config_path() -> Path:
    default = Path.home() / ".config" / "photo-backer-upper" / "config"
    return Path(os.environ.get("PHOTO_BACKER_UPPER_CONFIG", default)).expanduser()


def _file_values() -> dict:
    path = config_path()
    values = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def file_values() -> dict:
    """Settings from the config file only (what a launchd run sees)."""
    return _file_values()


def get(name: str, default=None):
    value = os.environ.get(name)
    if value:
        return value
    return _file_values().get(name) or default
