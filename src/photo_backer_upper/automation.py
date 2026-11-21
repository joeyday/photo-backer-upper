"""Pieces for unattended runs: NAS auto-mount, run lock, notifications, launchd agent."""

import fcntl
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from . import config

AGENT_LABEL = "com.photo-backer-upper.export"


class ConfigError(Exception):
    pass


class LockBusy(Exception):
    pass


# --- NAS mount -------------------------------------------------------------------------

def ensure_nas_mounted(archive_dir: Path) -> None:
    """If the archive folder is missing and a NAS URL is configured, try to mount it.

    Uses macOS's own mount call (same as Finder's Connect to Server), so credentials come
    from the login Keychain. The URL is never printed, since the logs may be shared.
    """
    if archive_dir.is_dir():
        return
    url = config.get("PHOTO_BACKER_UPPER_NAS_URL")
    if not url:
        return  # the caller's mount check reports the problem
    print("Archive not found; trying to mount the NAS...", flush=True)
    try:
        subprocess.run(
            ["osascript",
             "-e", "on run argv", "-e", "mount volume (item 1 of argv)", "-e", "end run", url],
            capture_output=True, text=True, timeout=90)
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"NAS mount attempt failed: {type(e).__name__}", flush=True)
    for _ in range(20):
        if archive_dir.is_dir():
            print("NAS mounted.", flush=True)
            return
        time.sleep(1)


# --- run lock --------------------------------------------------------------------------

@contextmanager
def run_lock(state_dir: Path):
    """Only one export/mirror at a time; a scheduled run that overlaps a long one just exits."""
    path = state_dir / "pback.lock"
    f = open(path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise LockBusy("another pback run is already in progress; exiting")
    try:
        f.write(str(os.getpid()))
        f.flush()
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


# --- notifications ---------------------------------------------------------------------

def notify(title: str, message: str) -> None:
    """Best-effort macOS notification; never raises."""
    try:
        subprocess.run(
            ["osascript",
             "-e", "on run argv", "-e",
             "display notification (item 2 of argv) with title (item 1 of argv)",
             "-e", "end run", title, message],
            capture_output=True, timeout=15)
    except Exception:
        pass


# --- mirror free-space guard -----------------------------------------------------------

TRANSFER_RE = re.compile(r"Total transferred file size:\s*([\d,]+)")


def bytes_to_transfer(rsync_cmd: list) -> int:
    """Dry-run the rsync command with --stats and return how many bytes it would copy."""
    out = subprocess.run(rsync_cmd + ["--dry-run", "--stats"],
                         capture_output=True, text=True).stdout
    m = TRANSFER_RE.search(out)
    if not m:
        raise ConfigError("couldn't work out how much the mirror would copy "
                          "(use --skip-space-check to mirror anyway)")
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
            f"with --delete, can leave the copy half-updated). Use a bigger drive.")


# --- launchd agent ---------------------------------------------------------------------

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
            "PATH": ":".join([str(Path.home() / ".local" / "bin"), "/opt/homebrew/bin",
                              "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
            "PYTHONUNBUFFERED": "1",
        },
    }


def install_agent(state_dir: Path, every_hours: float, load: bool) -> int:
    pback = shutil.which("pback")
    if not pback:
        raise ConfigError("'pback' is not on PATH; install it first with: uv tool install .")
    log_dir = state_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = agent_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(build_agent_plist(pback, log_dir, every_hours), f)
    print(f"Wrote {path}")
    print(f"Runs `pback export --notify` every {every_hours:g} h and at login; log: {log_dir / 'export.log'}")
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
