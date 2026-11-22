"""Run lock: only one export/mirror at a time."""

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path

from .errors import LockBusy


@contextmanager
def run_lock(state_dir: Path):
    """Only one export/mirror at a time; a scheduled run that overlaps a long one just exits.

    The lock file holds the holder's PID, and its modified time is when the lock was taken,
    so a run that finds it busy can tell how long the other one has been going.
    """
    path = state_dir / "pback.lock"
    # Held open across the yield on purpose, so no `with`.
    # Mode "a+", not "w": a run that loses the race mustn't wipe the holder's PID or time.
    f = open(path, "a+")  # noqa: SIM115
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        try:
            held = max(0.0, time.time() - path.stat().st_mtime)
        except OSError:
            held = 0.0
        raise LockBusy("another pback run is already in progress; exiting", held) from None
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
