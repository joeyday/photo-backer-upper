"""macOS notifications."""

import contextlib
import subprocess


def notify(title: str, message: str) -> None:
    """Best-effort macOS notification; never raises."""
    with contextlib.suppress(Exception):
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
