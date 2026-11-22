"""Rename files that an earlier export named from UTC instead of local capture time.

Before naming.py, export used osxphotos' `{created}`, which for items Photos stores as UTC
(no timezone) gave names hours off (sometimes the wrong day or month folder). This finds files
recorded in the local export database whose name doesn't match the current naming rule and
renames/moves them (keeping any `_edited` or ` (N)` suffix and the extension).

Report-only unless apply=True. Never overwrites: if the new name is taken it picks the next
free ` (N)`. Every rename is written to an undo log (the same format as fix-dates, so
`pback fix-dates --undo LOG` reverses it). Renamed items are added to the skip list so a later
export doesn't recreate them under the old names.
"""

import re
import unicodedata
from datetime import datetime
from pathlib import Path

from . import exportdb
from .atomic import write_text_atomic
from .library import load_library
from .naming import TIMESTAMP_FMT, TIMESTAMP_PATTERN, choose
from .reconcile import SKIP_FILE

NAME_RE = re.compile("^(" + TIMESTAMP_PATTERN + ")(.*)$")


def _lib_dates(library):
    dates = {}
    for p in load_library(library):
        if p.get("date"):
            o = datetime.fromisoformat(p["date_original"]) if p.get("date_original") else None
            dates[p["uuid"]] = (datetime.fromisoformat(p["date"]), o)
    return dates


def _taken(path: Path, planned: set) -> bool:
    return path.exists() or str(path).lower() in planned


def fix_export_names(main_dir: Path, state_dir: Path, library=None, apply=False) -> int:
    db = state_dir / exportdb.EXPORT_DB_FILE
    if not db.exists():
        print("No export database found; nothing to do.")
        return 0
    with exportdb.connect(db) as con:
        rows = exportdb.exported_files(con)
    dates = _lib_dates(library)

    plan, planned, other = (
        [],
        set(),
        {"already right": 0, "file missing": 0, "unexpected name": 0, "no library item": 0},
    )
    fixed_uuids = set()
    for uuid, rel in rows:
        if uuid not in dates:
            other["no library item"] += 1
            continue
        date, original = dates[uuid]
        old_base, new_dt = date.strftime(TIMESTAMP_FMT), choose(date, original)
        new_base = new_dt.strftime(TIMESTAMP_FMT)
        src = main_dir / unicodedata.normalize("NFC", rel)
        m = NAME_RE.match(Path(rel).stem)
        if not m or m.group(1) != old_base:
            other["unexpected name"] += 1
            continue
        if new_base == old_base:
            other["already right"] += 1
            continue
        if not src.exists():
            other["file missing"] += 1
            continue
        folder = main_dir / new_dt.strftime("%Y-%m")
        dst = folder / f"{new_base}{m.group(2)}{src.suffix}"
        n = 0
        while _taken(dst, planned):
            n += 1
            dst = folder / f"{new_base}{m.group(2)} ({n}){src.suffix}"
        planned.add(str(dst).lower())
        plan.append((uuid, src, dst))
        fixed_uuids.add(uuid)

    moved_month = sum(1 for _u, s, d in plan if s.parent.name != d.parent.name)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report = state_dir / f"fix-export-names-report-{stamp}.tsv"
    report.write_text(
        "".join(f"{u}\t{s.relative_to(main_dir)}\t{d.relative_to(main_dir)}\n" for u, s, d in plan)
    )
    print(f"Files recorded by earlier exports:      {len(rows)}")
    print(f"  to rename (UTC name -> local time):   {len(plan)} files for {len(fixed_uuids)} items")
    print(f"    of which move to another month:     {moved_month}")
    for k, v in other.items():
        print(f"  {k + ':':38} {v}")
    print(f"Plan written to {report}")
    if not apply:
        print("Report only; nothing renamed. Re-run with --apply.")
        return 0

    undo_log = state_dir / f"fix-export-names-{stamp}.tsv"
    done = 0
    with open(undo_log, "a", encoding="utf-8") as log:
        for _u, src, dst in plan:
            if not src.exists() or dst.exists():
                continue  # changed since planning; never overwrite
            dst.parent.mkdir(parents=True, exist_ok=True)
            size = src.stat().st_size
            src.rename(dst)
            if not dst.exists() or dst.stat().st_size != size:
                print(f"WARNING: check {dst}")
                continue
            log.write(f"{src}\t{dst}\n")
            log.flush()
            done += 1
    skip = state_dir / SKIP_FILE
    old_text = skip.read_text() if skip.exists() else ""
    have = set(old_text.split())
    if old_text and not old_text.endswith("\n"):
        old_text += "\n"
    write_text_atomic(skip, old_text + "".join(f"{u}\n" for u in sorted(fixed_uuids - have)))
    print(f"Renamed {done} files. Undo log: {undo_log}")
    print(f"Added {len(fixed_uuids - have)} items to the skip list so export leaves them alone.")
    return 0
