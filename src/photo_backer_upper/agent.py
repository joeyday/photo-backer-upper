"""The launchd agent that runs `pback export` on a schedule."""

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

from . import config
from .errors import ConfigError

AGENT_LABEL = "com.photo-backer-upper.export"


def launcher_executable() -> Path:
    return (
        Path.home()
        / "Applications"
        / "Photo Backer Upper.app"
        / "Contents"
        / "MacOS"
        / "PhotoBackerUpper"
    )


def agent_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{AGENT_LABEL}.plist"


def build_agent_plist(pback: str, log_dir: Path, every_hours: float) -> dict:
    return {
        "Label": AGENT_LABEL,
        "ProgramArguments": [pback, "export", "--notify"],
        "StartInterval": int(every_hours * 3600),
        "RunAtLoad": True,
        "StandardOutPath": str(log_dir / "export.log"),
        "StandardErrorPath": str(log_dir / "export.log"),
        # launchd's default PATH is tiny; osxphotos and Homebrew tools live elsewhere.
        "EnvironmentVariables": {
            "PATH": ":".join(
                [
                    str(Path.home() / ".local" / "bin"),
                    "/opt/homebrew/bin",
                    "/usr/local/bin",
                    "/usr/bin",
                    "/bin",
                    "/usr/sbin",
                    "/sbin",
                ]
            ),
            "PYTHONUNBUFFERED": "1",
        },
    }


def install_agent(state_dir: Path, every_hours: float, load: bool) -> int:
    if not shutil.which("pback"):
        raise ConfigError("'pback' is not on PATH; install it first with: uv tool install .")
    # The agent runs the signed launcher app, so macOS privacy grants (Photos, network
    # volumes) belong to that app alone and not to a shared Python interpreter.
    launcher = launcher_executable()
    if not launcher.exists():
        raise ConfigError(
            f"launcher app not found at {launcher}; build it first with: "
            "SIGN_IDENTITY=<identity> sh launcher/build.sh, then open it once "
            "in Finder to grant Photos access"
        )
    # launchd doesn't read ~/.zshrc, so the settings must be in the config file.
    if not config.file_values().get("PHOTO_BACKER_UPPER_MAIN_DIR"):
        raise ConfigError(
            f"PHOTO_BACKER_UPPER_MAIN_DIR is not set in {config.config_path()}; "
            "scheduled runs can't see ~/.zshrc, so put your settings in that file"
        )
    pback = str(launcher)
    log_dir = state_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = agent_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(build_agent_plist(pback, log_dir, every_hours), f)
    print(f"Wrote {path}")
    print(
        f"Runs `pback export --notify` every {every_hours:g} h and at login; "
        f"log: {log_dir / 'export.log'}"
    )
    target = f"gui/{os.getuid()}"
    if load:
        subprocess.run(["launchctl", "bootout", f"{target}/{AGENT_LABEL}"], capture_output=True)
        rc = subprocess.call(["launchctl", "bootstrap", target, str(path)])
        print("Loaded." if rc == 0 else "launchctl bootstrap failed.")
        return rc
    print(f"Not loaded yet. To start it: launchctl bootstrap {target} {path}")
    return 0


def uninstall_agent() -> int:
    target = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{target}/{AGENT_LABEL}"], capture_output=True)
    path = agent_plist_path()
    if path.exists():
        path.unlink()
        print(f"Removed {path}")
    else:
        print("No agent installed.")
    return 0
