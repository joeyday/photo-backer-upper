"""Pieces for unattended runs: network share auto-mount, run lock, notifications, launchd agent."""

import fcntl
import os
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from . import config

AGENT_LABEL = "com.photo-backer-upper.export"


class ConfigError(Exception):
    pass


class LockBusy(Exception):
    def __init__(self, message: str, held_seconds: float = 0):
        super().__init__(message)
        self.held_seconds = held_seconds


# --- network share mount ---------------------------------------------------------------

_DEFAULT_PORTS = {"smb": 445, "cifs": 445, "afp": 548, "nfs": 2049}
NAS_WAIT_SECONDS = 120


def nas_endpoint(url: str) -> tuple[str, int] | None:
    """Host and port to probe for a share URL like smb://user@host/Share, or None."""
    parts = urlsplit(url)
    if not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        port = None
    return parts.hostname, port or _DEFAULT_PORTS.get(parts.scheme.lower(), 445)


def wait_for_nas(url: str, timeout: float = NAS_WAIT_SECONDS) -> bool:
    """Wait until the share's server accepts connections, e.g. while the network comes up after boot.

    Returns True as soon as it answers, False if it never does within `timeout` seconds.
    Unparseable URLs return True so the mount call still gets a chance.
    """
    endpoint = nas_endpoint(url)
    if endpoint is None:
        return True
    deadline = time.monotonic() + timeout
    announced = False
    while True:
        try:
            with socket.create_connection(endpoint, timeout=3):
                return True
        except OSError:
            pass
        if time.monotonic() >= deadline:
            return False
        if not announced:
            print("Network share not reachable yet; waiting for the network...", flush=True)
            announced = True
        time.sleep(5)


def ensure_nas_mounted(archive_dir: Path) -> None:
    """If the archive folder is missing and a share URL is configured, try to mount it.

    Uses macOS's own mount call (same as Finder's Connect to Server), so credentials come
    from the login Keychain. The URL is never printed, since the logs may be shared.
    """
    if archive_dir.is_dir():
        return
    url = config.get("PHOTO_BACKER_UPPER_NAS_URL")
    if not url:
        return  # the caller's mount check reports the problem
    if not wait_for_nas(url):
        # Don't call mount: it would pop up a "can't connect" dialog nobody is there to see.
        print(f"Network share not reachable after {NAS_WAIT_SECONDS}s; skipping mount.", flush=True)
        return
    print("Archive not found; trying to mount the network share...", flush=True)
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                "on run argv",
                "-e",
                "mount volume (item 1 of argv)",
                "-e",
                "end run",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"Network share mount attempt failed: {type(e).__name__}", flush=True)
    for _ in range(20):
        if archive_dir.is_dir():
            print("Network share mounted.", flush=True)
            return
        time.sleep(1)


# --- run lock --------------------------------------------------------------------------


@contextmanager
def run_lock(state_dir: Path):
    """Only one export/mirror at a time; a scheduled run that overlaps a long one just exits.

    The lock file holds the holder's PID, and its modified time is when the lock was taken,
    so a run that finds it busy can tell how long the other one has been going.
    """
    path = state_dir / "pback.lock"
    f = open(path, "a+")  # not "w": a run that loses the race mustn't wipe the holder's PID or time
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        try:
            held = max(0.0, time.time() - path.stat().st_mtime)
        except OSError:
            held = 0.0
        raise LockBusy("another pback run is already in progress; exiting", held)
    try:
        f.seek(0)
        f.truncate()
        f.write(str(os.getpid()))
        f.flush()
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


# A run that finds the lock held this long warns that the holder may be stuck. Normal runs
# take minutes; a first export or a huge iCloud download can take hours.
STUCK_HOURS = 4


# --- notifications ---------------------------------------------------------------------


def notify(title: str, message: str) -> None:
    """Best-effort macOS notification; never raises."""
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                "on run argv",
                "-e",
                "display notification (item 2 of argv) with title (item 1 of argv)",
                "-e",
                "end run",
                title,
                message,
            ],
            capture_output=True,
            timeout=15,
        )
    except Exception:
        pass


# --- mirror free-space guard -----------------------------------------------------------

TRANSFER_RE = re.compile(r"Total transferred file size:\s*([\d,]+)")


def bytes_to_transfer(rsync_cmd: list) -> int:
    """Dry-run the rsync command with --stats and return how many bytes it would copy."""
    out = subprocess.run(
        rsync_cmd + ["--dry-run", "--stats"], capture_output=True, text=True
    ).stdout
    m = TRANSFER_RE.search(out)
    if not m:
        raise ConfigError(
            "couldn't work out how much the mirror would copy "
            "(use --skip-space-check to mirror anyway)"
        )
    return int(m.group(1).replace(",", ""))


def check_free_space(rsync_cmd: list, dest: Path) -> None:
    need = bytes_to_transfer(rsync_cmd)
    free = shutil.disk_usage(dest).free
    gib = 1 << 30
    print(f"Mirror would copy {need / gib:.0f} GiB; {free / gib:.0f} GiB free.", flush=True)
    if need > free:
        raise ConfigError(
            f"the mirror would copy {need / gib:.0f} GiB but the secondary drive has only "
            f"{free / gib:.0f} GiB free. Not starting (a full drive mid-mirror, especially "
            f"with --delete, can leave the copy half-updated). Use a bigger drive."
        )


# --- launchd agent ---------------------------------------------------------------------


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
        f"Runs `pback export --notify` every {every_hours:g} h and at login; log: {log_dir / 'export.log'}"
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
