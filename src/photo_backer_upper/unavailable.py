"""Live Photo videos that iCloud never delivers.

For a few edited Live Photos, iCloud never provides the edited video. The edited still is
archived fine, but since the `.mov` never reaches the archive, osxphotos re-downloads the
photo from iCloud on every run, fails to get the video, and records it as missing again.

To stop that, export records these UUIDs in a list. The main export skips them, and a
second pass exports them with `--skip-live`, so their stills are still kept up to date
(e.g. after a new edit) without fetching anything that's already archived. The list is
cleared after RETRY_DAYS without changes so each video gets another chance in case iCloud
starts providing it.
"""

import sqlite3
import time
from pathlib import Path

from .atomic import write_text_atomic

LIVE_UNAVAILABLE_FILE = "live-unavailable-uuids.txt"
MERGED_SKIP_FILE = "skip-uuids.merged.txt"
RETRY_DAYS = 30


def read_uuids(path: Path) -> list[str]:
    if not path.exists():
        return []
    lines = (line.strip() for line in path.read_text().splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def load_live_unavailable(state_dir: Path, now: float | None = None) -> list[str]:
    """The current list, or [] (and the file removed) once it's due for a retry."""
    path = state_dir / LIVE_UNAVAILABLE_FILE
    if not path.exists():
        return []
    now = time.time() if now is None else now
    if now - path.stat().st_mtime > RETRY_DAYS * 86400:
        path.unlink()
        return []
    return read_uuids(path)


def missing_live_uuids(export_db: Path, since_utc: str) -> set[str]:
    """Live Photos whose edited `_edited.mov` export recorded as missing at or after `since_utc`.

    Only the edited video counts. An original Live Photo video that fails once may just be a
    passing iCloud hiccup, and listing it would export the item with `--skip-live` and so
    silence the alert while the original video stays unarchived.
    Only UUIDs that also have an archived still count, so a plain video that fails to
    download (its own file is a `.mov`) isn't mistaken for a Live Photo.
    `since_utc` is "YYYY-MM-DD HH:MM:SS", the same format osxphotos' history table uses.
    """
    if not export_db.exists():
        return set()
    con = sqlite3.connect(f"file:{export_db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select hp.uuid, hp.filepath_normalized from history h "
            "join history_path hp on hp.id = h.filepath_id "
            "where h.action = 'missing' and h.datetime >= ?", (since_utc,)).fetchall()
        stills = {uuid for uuid, path in con.execute(
            "select uuid, filepath_normalized from export_data")
            if not path.lower().endswith(".mov")}
    finally:
        con.close()
    return {uuid for uuid, path in rows if path.lower().endswith("_edited.mov") and uuid in stills}


def remember_live_unavailable(state_dir: Path, uuids: set[str]) -> list[str]:
    """Add `uuids` to the list; returns the ones that weren't already in it."""
    if not uuids:
        return []
    path = state_dir / LIVE_UNAVAILABLE_FILE
    existing = read_uuids(path)
    new = sorted(uuids - set(existing))
    if new:
        # Rewritten whole, not appended: osxphotos rejects the file if a line is cut short.
        header = [
            "# Live Photos whose video iCloud never provides; exported with --skip-live.",
            "# Written by `pback export`. Delete this file to retry them on the next run.",
        ]
        write_text_atomic(path, "".join(line + "\n" for line in header + existing + new))
    return new


def write_merged_skip(state_dir: Path, *lists: list[str]) -> Path:
    """One skip file from several lists, since osxphotos takes only one --skip-uuid-from-file."""
    path = state_dir / MERGED_SKIP_FILE
    seen = dict.fromkeys(u for lst in lists for u in lst)
    write_text_atomic(path, "".join(u + "\n" for u in seen))
    return path
