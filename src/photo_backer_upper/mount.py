"""Network share auto-mount for the main archive."""

import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

from . import config

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
    """Wait until the share's server accepts connections, e.g. while the network comes up after
    boot.

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
