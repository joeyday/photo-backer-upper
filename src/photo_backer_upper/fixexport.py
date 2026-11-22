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
from .renames import Rename, undo_log

NAME_RE = re.compile("^(" + TIMESTAMP_PATTERN + ")(.*)$")


def _lib_dates(library):
    dates = {}
    for photo in load_library(library):
        if photo.get("date"):
            original = (
                datetime.fromisoformat(photo["date_original"])
                if photo.get("date_original")
                else None
            )
            dates[photo["uuid"]] = (datetime.fromisoformat(photo["date"]), original)
    return dates


def _taken(path: Path, planned: set) -> bool:
    return path.exists() or str(path).lower() in planned


def free_name(folder: Path, base: str, ext: str, planned: set) -> Path:
    """`base` + `ext` in `folder`, or the first ` (N)` variant not on disk and not planned."""
    dst = folder / f"{base}{ext}"
    n = 0
    while _taken(dst, planned):
        n += 1
        dst = folder / f"{base} ({n}){ext}"
    return dst


def plan_row(main_dir: Path, uuid: str, rel: str, dates: dict, planned: set) -> Rename | str:
    """Decide what to do with one exported file: a Rename, or the counter it falls under.

    A Rename claims its new name in `planned`.
    """
    if uuid not in dates:
        return "no library item"
    date, original = dates[uuid]
    old_base, new_dt = date.strftime(TIMESTAMP_FMT), choose(date, original)
    new_base = new_dt.strftime(TIMESTAMP_FMT)
    src = main_dir / unicodedata.normalize("NFC", rel)
    m = NAME_RE.match(Path(rel).stem)
    if not m or m.group(1) != old_base:
        return "unexpected name"
    if new_base == old_base:
        return "already right"
    if not src.exists():
        return "file missing"
    dst = free_name(main_dir / new_dt.strftime("%Y-%m"), new_base + m.group(2), src.suffix, planned)
    planned.add(str(dst).lower())
    return Rename(src, dst, uuid=uuid)


def plan_renames(main_dir: Path, rows: list, dates: dict) -> tuple[list, dict]:
    """The Renames, in export-database order, and how many files fall under each other counter."""
    plan, planned = [], set()
    other = {"already right": 0, "file missing": 0, "unexpected name": 0, "no library item": 0}
    for uuid, rel in rows:
        entry = plan_row(main_dir, uuid, rel, dates, planned)
        if isinstance(entry, Rename):
            plan.append(entry)
        else:
            other[entry] += 1
    return plan, other


def write_report(main_dir: Path, report: Path, plan: list) -> None:
    report.write_text(
        "".join(
            f"{r.uuid}\t{r.old.relative_to(main_dir)}\t{r.new.relative_to(main_dir)}\n"
            for r in plan
        )
    )


def print_summary(rows: list, plan: list, other: dict, report: Path) -> None:
    items = len({r.uuid for r in plan})
    moved_month = sum(1 for r in plan if r.old.parent.name != r.new.parent.name)
    print(f"Files recorded by earlier exports:      {len(rows)}")
    print(f"  to rename (UTC name -> local time):   {len(plan)} files for {items} items")
    print(f"    of which move to another month:     {moved_month}")
    for label, count in other.items():
        print(f"  {label + ':':38} {count}")
    print(f"Plan written to {report}")


def apply_renames(plan: list, log: Path) -> int:
    """Rename the files, recording each in the undo log; returns how many were renamed.

    A file whose size doesn't match after its rename gets a warning and no undo-log line.
    """
    done = 0
    with undo_log(log) as record:
        for rename in plan:
            if not rename.old.exists() or rename.new.exists():
                continue  # changed since planning; never overwrite
            rename.new.parent.mkdir(parents=True, exist_ok=True)
            size = rename.old.stat().st_size
            rename.old.rename(rename.new)
            if not rename.new.exists() or rename.new.stat().st_size != size:
                print(f"WARNING: check {rename.new}")
                continue
            record(rename)
            done += 1
    return done


def add_to_skip_list(state_dir: Path, uuids: set) -> int:
    """Add the UUIDs not already in the skip list; returns how many were added."""
    skip = state_dir / SKIP_FILE
    old_text = skip.read_text() if skip.exists() else ""
    have = set(old_text.split())
    if old_text and not old_text.endswith("\n"):
        old_text += "\n"
    write_text_atomic(skip, old_text + "".join(f"{u}\n" for u in sorted(uuids - have)))
    return len(uuids - have)


def fix_export_names(main_dir: Path, state_dir: Path, library=None, apply=False) -> int:
    db_path = state_dir / exportdb.EXPORT_DB_FILE
    if not db_path.exists():
        print("No export database found; nothing to do.")
        return 0
    with exportdb.connect(db_path) as con:
        rows = exportdb.exported_files(con)
    dates = _lib_dates(library)

    plan, other = plan_renames(main_dir, rows, dates)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report = state_dir / f"fix-export-names-report-{stamp}.tsv"
    write_report(main_dir, report, plan)
    print_summary(rows, plan, other, report)
    if not apply:
        print("Report only; nothing renamed. Re-run with --apply.")
        return 0

    undo_path = state_dir / f"fix-export-names-{stamp}.tsv"
    done = apply_renames(plan, undo_path)
    added = add_to_skip_list(state_dir, {r.uuid for r in plan})
    print(f"Renamed {done} files. Undo log: {undo_path}")
    print(f"Added {added} items to the skip list so export leaves them alone.")
    return 0
