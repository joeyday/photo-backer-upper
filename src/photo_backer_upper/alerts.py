"""Alert when an export leaves photos or videos out of the archive.

osxphotos counts an item it couldn't get from iCloud as "missing" but still exits
successfully, so without this a failed download looks like a normal run.

Ignored: edited Live Photo videos (`*_edited.mov` next to an archived still), which iCloud
often doesn't have and which don't matter here, and `.AAE` adjustment sidecars.
Each missing file is alerted when first seen and again every REALERT_DAYS while it stays
missing; it's dropped from the state once it's no longer reported missing.
"""

import json
import sqlite3
import time
from pathlib import Path, PurePosixPath

from .atomic import write_text_atomic

ALERT_STATE_FILE = "missing-alerts.json"
REALERT_DAYS = 7


def _is_ignored(path: str, stills: set[str], uuid: str) -> bool:
    p = PurePosixPath(path.lower())
    if p.suffix == ".aae":
        return True
    return p.suffix == ".mov" and p.stem.endswith("_edited") and uuid in stills


def missing_this_run(export_db: Path, since_utc: str) -> list[str]:
    """Archive paths (relative) that export recorded as missing at or after `since_utc`."""
    if not export_db.exists():
        return []
    con = sqlite3.connect(f"file:{export_db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select distinct hp.uuid, hp.filepath_normalized from history h "
            "join history_path hp on hp.id = h.filepath_id "
            "where h.action = 'missing' and h.datetime >= ?", (since_utc,)).fetchall()
        stills = {uuid for uuid, path in con.execute(
            "select uuid, filepath_normalized from export_data")
            if not path.lower().endswith(".mov")}
    finally:
        con.close()
    return sorted(path for uuid, path in rows if not _is_ignored(path, stills, uuid))


def export_errors(report: Path) -> list[tuple[str, str]]:
    """(file, message) for each file osxphotos failed to export, read from its `--report` database.

    osxphotos counts these as `error: N` in its summary but still exits successfully.
    """
    if not report.exists():
        return []
    con = sqlite3.connect(f"file:{report}?mode=ro", uri=True)
    try:
        return con.execute(
            "select filename, error from report where error is not null and error != '' "
            "order by filename").fetchall()
    finally:
        con.close()


def due_alerts(state_dir: Path, missing: list[str], now: float | None = None) -> list[str]:
    """The missing files to alert about now; updates the saved alert state."""
    now = time.time() if now is None else now
    path = state_dir / ALERT_STATE_FILE
    try:
        last = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        last = {}
    due = [m for m in missing if m not in last or now - last[m] >= REALERT_DAYS * 86400]
    state = {m: (now if m in due else last[m]) for m in missing}
    write_text_atomic(path, json.dumps(state, indent=1, sort_keys=True))
    return due


def describe(files: list[str]) -> str:
    videos = sum(1 for f in files if f.lower().endswith((".mov", ".mp4", ".m4v")))
    photos = len(files) - videos
    parts = [f"{n} {word}{'s' if n != 1 else ''}"
             for n, word in ((photos, "photo"), (videos, "video")) if n]
    verb = "isn't" if len(files) == 1 else "aren't"
    return " and ".join(parts) + f" couldn't be downloaded from iCloud and {verb} backed up yet (retrying next run)"
