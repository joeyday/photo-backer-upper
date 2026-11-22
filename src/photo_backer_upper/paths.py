"""Where things live, and the checks and helpers every command shares."""

import shutil
import subprocess
from pathlib import Path

from . import config
from .errors import ConfigError, SecondaryUnavailable
from .mount import ensure_nas_mounted


def env_dir(name: str) -> Path:
    value = config.get(name)
    if not value:
        raise ConfigError(f"{name} is not set (see .env.example)")
    return Path(value).expanduser()


def require_mounted(path: Path, label: str) -> None:
    """The archive's parent volume must exist, so we never write into an unmounted mount point."""
    if not path.is_dir():
        raise ConfigError(f"{label} not found at {path}; is the volume mounted?")


def main_archive() -> Path:
    """The main backup location, mounting its network share first if a share URL is configured."""
    main_dir = env_dir("PHOTO_BACKER_UPPER_MAIN_DIR")
    ensure_nas_mounted(main_dir)
    require_mounted(main_dir, "Main archive")
    return main_dir


def secondary_archive() -> Path:
    try:
        secondary_dir = env_dir("PHOTO_BACKER_UPPER_SECONDARY_DIR")
        # Require the volume itself (e.g. /Volumes/Secondary), not just the subfolder, so a
        # missing drive is never "created" as a plain folder on the startup disk.
        if len(secondary_dir.parts) >= 3 and secondary_dir.parts[1] == "Volumes":
            require_mounted(Path(*secondary_dir.parts[:3]), "Secondary drive")
    except ConfigError as e:
        raise SecondaryUnavailable(str(e)) from None
    return secondary_dir


def require_tool(name: str, hint: str) -> None:
    if shutil.which(name) is None:
        raise ConfigError(f"'{name}' not found on PATH. {hint}")


def state_dir() -> Path:
    default = Path.home() / ".local" / "state" / "photo-backer-upper"
    path = Path(config.get("PHOTO_BACKER_UPPER_STATE_DIR", default)).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_cmd(cmd: list) -> int:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.call(cmd)
