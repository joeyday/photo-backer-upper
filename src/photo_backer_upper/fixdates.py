"""Rename archive files to their real capture date: YYYY-MM-DD_HH-MM-SS.ext

Folders: a file in a folder named exactly YYYY-MM is also moved to the folder for its
corrected month. A file in any other folder (e.g. "2005-01 Family Trip") is renamed
in place and never moved. Files loose at the archive root are not touched at all.

Report-only unless apply=True. Every rename is appended to a log so it can be undone.
The full plan is always written to a report file; the terminal gets a summary.

What is eligible:
  videos   Any video whose name is date-style, mod_-prefixed, or camera-style, and whose
           name differs from the local capture time (QuickTime Keys:CreationDate). Videos
           with only a UTC CreateDate are reported as approximate, not renamed.
  photos   Only photos with camera-style names (IMG_1234, DSC_0001, PXL_..., 100_0789).
           Renamed from DateTimeOriginal (or CreateDate). Photos already named with a
           capture time are left alone.

Never renamed: osxphotos' `<date>_edited` copies (renaming one would lose the suffix and
make the next export copy it in again), mod_ images (derived files whose date is
unreliable), files with descriptive names (unless include_other_names), files with no
usable embedded date, and anything whose rename would sit next to an existing same-sized
file (probable duplicate: reported instead).
"""

import json
import os
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .media import PHOTO_EXTS, VIDEO_EXTS
from .naming import TIMESTAMP_FMT, TIMESTAMP_PATTERN
from .renames import Rename, undo_log

STAMP_RE = re.compile("^" + TIMESTAMP_PATTERN + "$")
DATE_NAME_RE = re.compile(r"^(?:mod_)?(" + TIMESTAMP_PATTERN + r")(?:-\d+| \(\d+\))?$")
CAMERA_NAME_RE = re.compile(
    r"^(?:(?:IMG|DSC[NF]?|PXL|MVI|VID|PICT|SAM|P)[_-]?\d|\d{3}[_-]\d{4}|\d{8}[_-]\d{6}"
    r"|\d{4}[-_.]\d{2}[-_.]\d{2}[ _-]\d{2}[-_.]\d{2}[-_.]\d{2})",
    re.I,
)
MONTH_DIR_RE = re.compile(r"^\d{4}-\d{2}$")
# osxphotos' name for the edited copy of an item: "<date>_edited", maybe with " (N)".
EDITED_NAME_RE = re.compile("^" + TIMESTAMP_PATTERN + r"_edited(?: \(\d+\))?$")
CHUNK = 200


def valid_stamp(value) -> bool:
    if not isinstance(value, str) or not STAMP_RE.match(value):
        return False
    try:
        return datetime.strptime(value, TIMESTAMP_FMT).year >= 1990
    except ValueError:
        return False


def scan(main_dir: Path, videos: bool, photos: bool) -> list:
    out = []
    for root, _dirs, files in os.walk(main_dir):
        for name in files:
            if name.startswith(".") or EDITED_NAME_RE.match(Path(name).stem):
                continue
            ext = Path(name).suffix.lower()
            if (videos and ext in VIDEO_EXTS) or (photos and ext in PHOTO_EXTS):
                out.append(Path(root) / name)
    return sorted(out)


def exiftool_json(paths: list, tags: list, group: bool) -> dict:
    rows = {}
    for i in range(0, len(paths), CHUNK):
        chunk = paths[i : i + CHUNK]
        cmd = ["exiftool", "-json", "-d", TIMESTAMP_FMT, "-m", "-fast", *tags, *map(str, chunk)]
        if group:
            cmd.insert(2, "-G1")
        proc = subprocess.run(cmd, capture_output=True, text=True)
        try:
            data = json.loads(proc.stdout) if proc.stdout.strip() else []
        except json.JSONDecodeError:
            data = []
        for row in data:
            rows[Path(row["SourceFile"])] = row
        print(f"  read metadata: {min(i + CHUNK, len(paths))}/{len(paths)}", end="\r", flush=True)
    print()
    return rows


def classify_name(path: Path, include_other: bool) -> str:
    """'date', 'camera' or 'other' (descriptive)."""
    stem = path.stem
    if DATE_NAME_RE.match(stem):
        return "date"
    if CAMERA_NAME_RE.match(stem):
        return "camera"
    return "camera" if include_other else "other"


def target_folder(main_dir: Path, path: Path, stamp: str) -> Path:
    """Where a renamed file should live.

    In a folder named exactly YYYY-MM: the folder for the corrected month, as a sibling of
    the current one. In any other folder (e.g. "2005-01 Family Trip", "Relatives"): stay
    put, since the user curates those by hand. (Files loose at the archive root are skipped
    before this is called.)
    """
    parent = path.parent
    if MONTH_DIR_RE.match(parent.name):
        return parent.parent / stamp[:7]
    return parent


def free_target(folder: Path, stamp: str, ext: str, size: int, taken: dict):
    """Return (target, None) or (None, existing_path) if a same-sized file is already there."""
    cand = folder / f"{stamp}{ext}"
    n = 1
    while cand.exists() or cand in taken:
        other_size = taken.get(cand)
        if other_size is None:
            try:
                other_size = cand.stat().st_size
            except OSError:
                other_size = -1
        if other_size == size:
            return None, cand
        cand = folder / f"{stamp} ({n}){ext}"
        n += 1
    return cand, None


@dataclass(frozen=True)
class Duplicate:
    """A file left alone because a same-sized file already has its new name. Reported only."""

    kind: str
    path: Path
    existing: Path


def name_skip_reason(main_dir: Path, path: Path, cls: str, kind: str) -> str | None:
    """Why a file is left alone because of its name or place, or None if it may be renamed."""
    is_mod = path.name.startswith("mod_")
    if path.parent == main_dir:
        return "loose at archive root (left alone)"
    if is_mod and not DATE_NAME_RE.match(path.stem):
        return "unrecognized mod_ name"
    if cls == "other":
        return "descriptive name (left alone)"
    if kind == "photo" and cls == "date" and not is_mod:
        return "photo already date-named"
    if kind == "photo" and is_mod:
        return "mod_ image"
    return None


def embedded_stamp(row: dict, kind: str) -> tuple[str | None, str | None]:
    """(capture time, None) from a file's metadata, or (None, why it has no usable one)."""
    if kind == "photo":
        # An unusable DateTimeOriginal is not passed over for CreateDate.
        stamp = row.get("DateTimeOriginal") or row.get("CreateDate")
        utc_only = False
    else:
        stamp = row.get("Keys:CreationDate")
        utc_only = not valid_stamp(stamp) and valid_stamp(row.get("QuickTime:CreateDate"))
    if valid_stamp(stamp):
        return stamp, None
    if utc_only:
        return None, "video: UTC time only (approximate)"
    return None, f"{kind}: no usable embedded date"


def file_size(path: Path, kind: str) -> tuple[int | None, str | None]:
    """(size, None), or (None, why) if the file can't be read or is empty."""
    try:
        size = path.stat().st_size
    except OSError:
        return None, f"{kind}: unreadable"
    if size == 0:
        return None, f"{kind}: empty file"
    return size, None


def rename_reason(name_match, stamp: str) -> str:
    """The report's reason for a rename: how far a date name is off, or "camera name"."""
    if not name_match:
        return "camera name"
    old = datetime.strptime(name_match.group(1), TIMESTAMP_FMT)
    delta = abs((old - datetime.strptime(stamp, TIMESTAMP_FMT)).total_seconds())
    if delta and delta % 3600 == 0 and delta <= 14 * 3600:
        return "UTC-offset"
    return "small drift" if delta <= 120 else "far off"


def plan_file(main_dir, path, row, kind, include_other, taken) -> str | Rename | Duplicate:
    """Decide what to do with one file: a Rename, a Duplicate, or the reason it is skipped.

    A Rename claims its new name in `taken` (path -> size).
    """
    cls = classify_name(path, include_other)
    reason = name_skip_reason(main_dir, path, cls, kind)
    if reason:
        return reason
    stamp, reason = embedded_stamp(row, kind)
    if reason:
        return reason
    m = DATE_NAME_RE.match(path.stem)
    if m and m.group(1) == stamp and not path.name.startswith("mod_"):
        return f"{kind} already correct"
    size, reason = file_size(path, kind)
    if reason:
        return reason
    target, dup = free_target(
        target_folder(main_dir, path, stamp), stamp, path.suffix.lower(), size, taken
    )
    if target is None:
        return Duplicate(kind, path, dup)
    taken[target] = size
    return Rename(path, target, kind=kind, reason=rename_reason(m, stamp))


def plan_files(main_dir, paths, info, kind, include_other, taken, plan, skipped):
    """Add each file's Rename or Duplicate to `plan`, and count the skipped ones in `skipped`."""
    for path in paths:
        entry = plan_file(main_dir, path, info.get(path, {}), kind, include_other, taken)
        if isinstance(entry, str):
            skipped[entry] += 1
            continue
        if isinstance(entry, Duplicate):
            skipped["probable duplicate of existing file (left alone)"] += 1
        plan.append(entry)


def photos_to_read(main_dir: Path, include_other: bool, skipped: Counter) -> list:
    """The photos worth reading metadata for; date-named and mod_ ones are counted as skipped."""
    imgs = []
    for p in scan(main_dir, False, True):
        if p.name.startswith("mod_"):
            skipped["mod_ image"] += 1
        elif classify_name(p, include_other) == "date":
            skipped["photo already date-named"] += 1
        else:
            imgs.append(p)
    return imgs


def plan_renames(main_dir: Path, videos: bool, photos: bool, include_other: bool):
    """Scan the archive. Returns the plan (Renames and Duplicates, in report order) and the
    count of files skipped for each reason."""
    plan, skipped, taken = [], Counter(), {}
    if videos:
        print("Scanning videos...")
        vids = scan(main_dir, True, False)
        info = exiftool_json(vids, ["-Keys:CreationDate", "-QuickTime:CreateDate"], group=True)
        plan_files(main_dir, vids, info, "video", include_other, taken, plan, skipped)
    if photos:
        print("Scanning photos...")
        # Only camera-style (non-date) photos need metadata; date-named and mod_ ones are left
        # alone.
        imgs = photos_to_read(main_dir, include_other, skipped)
        info = exiftool_json(imgs, ["-DateTimeOriginal", "-CreateDate"], group=False)
        plan_files(main_dir, imgs, info, "photo", include_other, taken, plan, skipped)
    return plan, skipped


def report_line(main_dir: Path, entry: Rename | Duplicate) -> str:
    if isinstance(entry, Duplicate):
        action, old, new, why = "duplicate", entry.path, entry.existing, ""
    else:
        action, old, new, why = "rename", entry.old, entry.new, entry.reason
    return (
        f"{entry.kind}\t{action}\t{old.relative_to(main_dir)}\t{new.relative_to(main_dir)}\t{why}\n"
    )


def write_report(main_dir: Path, report: Path, plan: list) -> None:
    with open(report, "w") as f:
        f.write("kind\taction\told\tnew\treason\n")
        for entry in plan:
            f.write(report_line(main_dir, entry))


def print_summary(main_dir: Path, renames: list, skipped: Counter, report: Path) -> None:
    print("\nWill rename:")
    for kind in ("video", "photo"):
        sel = [r for r in renames if r.kind == kind]
        if sel:
            print(f"  {kind}s: {len(sel)}  ({dict(Counter(r.reason for r in sel))})")
    print(f"  total: {len(renames)}")
    moved = sum(1 for r in renames if r.old.parent != r.new.parent)
    print(
        f"  of which move to a different month folder: {moved}; "
        f"renamed in place: {len(renames) - moved}"
    )
    print("Not renamed:")
    for reason, n in skipped.most_common():
        print(f"  {n:>7}  {reason}")
    print("Samples:")
    for r in renames[:5] + renames[5:][-5:]:
        print(f"  {r.old.relative_to(main_dir)} -> {r.new.relative_to(main_dir)}  [{r.reason}]")
    print(f"Full plan: {report}")


def apply_renames(renames: list, log: Path) -> tuple[int, list]:
    """Rename the files, recording each in the undo log. Returns how many were renamed and the
    old paths of those skipped because they changed since the scan."""
    done, changed = 0, []
    with undo_log(log) as record:
        for r in renames:
            # The plan may be stale (the scan can take a long time): never overwrite.
            if not r.old.exists() or r.new.exists():
                changed.append(r.old)
                continue
            r.new.parent.mkdir(parents=True, exist_ok=True)
            os.rename(r.old, r.new)
            record(r)
            done += 1
    return done, changed


def print_applied(main_dir: Path, done: int, changed: list, log: Path) -> None:
    print(f"Renamed {done} files. Undo log: {log}")
    if changed:
        print(
            f"Skipped {len(changed)} that changed since the scan (source gone or new name taken); "
            "run fix-dates again for them:"
        )
        for old in changed[:20]:
            print(f"  {old.relative_to(main_dir)}")


def fix_dates(
    main_dir: Path,
    state_dir: Path,
    apply: bool = False,
    videos: bool = True,
    photos: bool = True,
    include_other_names: bool = False,
) -> int:
    state_dir.mkdir(parents=True, exist_ok=True)
    plan, skipped = plan_renames(main_dir, videos, photos, include_other_names)
    renames = [entry for entry in plan if isinstance(entry, Rename)]
    stamp_now = datetime.now().strftime("%Y%m%d-%H%M%S")
    report = state_dir / f"fix-dates-report-{stamp_now}.tsv"
    write_report(main_dir, report, plan)
    print_summary(main_dir, renames, skipped, report)

    if not apply:
        print("Report only. Re-run with --apply to rename.")
        return 0
    if not renames:
        return 0
    log = state_dir / f"fix-dates-{stamp_now}.tsv"
    done, changed = apply_renames(renames, log)
    print_applied(main_dir, done, changed, log)
    return 0


def undo(log: Path) -> int:
    done = 0
    for line in reversed(log.read_text().splitlines()):
        old, new = line.split("\t")
        if Path(new).exists() and not Path(old).exists():
            os.rename(new, old)
            done += 1
    print(f"Restored {done} files from {log}")
    return 0
