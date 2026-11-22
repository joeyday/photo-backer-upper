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
from datetime import datetime
from pathlib import Path

VIDEO_EXTS = {".mov", ".mp4", ".m4v"}
PHOTO_EXTS = {".jpg", ".jpeg", ".heic", ".png", ".tif", ".tiff", ".gif", ".webp", ".avif"}
DATE_FMT = "%Y-%m-%d_%H-%M-%S"
STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")
DATE_NAME_RE = re.compile(r"^(?:mod_)?(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})(?:-\d+| \(\d+\))?$")
CAMERA_NAME_RE = re.compile(
    r"^(?:(?:IMG|DSC[NF]?|PXL|MVI|VID|PICT|SAM|P)[_-]?\d|\d{3}[_-]\d{4}|\d{8}[_-]\d{6}"
    r"|\d{4}[-_.]\d{2}[-_.]\d{2}[ _-]\d{2}[-_.]\d{2}[-_.]\d{2})",
    re.I,
)
MONTH_DIR_RE = re.compile(r"^\d{4}-\d{2}$")
# osxphotos' name for the edited copy of an item: "<date>_edited", maybe with " (N)".
EDITED_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_edited(?: \(\d+\))?$")
CHUNK = 200


def valid_stamp(value) -> bool:
    if not isinstance(value, str) or not STAMP_RE.match(value):
        return False
    try:
        return datetime.strptime(value, DATE_FMT).year >= 1990
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
        cmd = ["exiftool", "-json", "-d", DATE_FMT, "-m", "-fast", *tags, *map(str, chunk)]
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


def plan_files(main_dir, paths, info, kind, include_other, taken, plan, skipped):
    for path in paths:
        row = info.get(path, {})
        if path.parent == main_dir:
            skipped["loose at archive root (left alone)"] += 1
            continue
        cls = classify_name(path, include_other)
        if path.name.startswith("mod_") and not DATE_NAME_RE.match(path.stem):
            skipped["unrecognized mod_ name"] += 1
            continue
        if cls == "other":
            skipped["descriptive name (left alone)"] += 1
            continue
        if kind == "photo":
            if cls == "date" and not path.name.startswith("mod_"):
                skipped["photo already date-named"] += 1
                continue
            if path.name.startswith("mod_"):
                skipped["mod_ image"] += 1
                continue
            stamp = row.get("DateTimeOriginal") or row.get("CreateDate")
            utc_only = False
        else:
            stamp = row.get("Keys:CreationDate")
            utc_only = not valid_stamp(stamp) and valid_stamp(row.get("QuickTime:CreateDate"))
        if not valid_stamp(stamp):
            skipped[
                "video: UTC time only (approximate)"
                if utc_only
                else f"{kind}: no usable embedded date"
            ] += 1
            continue
        m = DATE_NAME_RE.match(path.stem)
        if m and m.group(1) == stamp and not path.name.startswith("mod_"):
            skipped[f"{kind} already correct"] += 1
            continue
        try:
            size = path.stat().st_size
        except OSError:
            skipped[f"{kind}: unreadable"] += 1
            continue
        if size == 0:
            skipped[f"{kind}: empty file"] += 1
            continue
        target, dup = free_target(
            target_folder(main_dir, path, stamp), stamp, path.suffix.lower(), size, taken
        )
        if target is None:
            skipped["probable duplicate of existing file (left alone)"] += 1
            plan.append((kind, "duplicate", path, dup, ""))
            continue
        taken[target] = size
        if m:
            old = datetime.strptime(m.group(1), DATE_FMT)
            delta = abs((old - datetime.strptime(stamp, DATE_FMT)).total_seconds())
            why = (
                "UTC-offset"
                if delta and delta % 3600 == 0 and delta <= 14 * 3600
                else ("small drift" if delta <= 120 else "far off")
            )
        else:
            why = "camera name" if cls == "camera" else "mod_"
        plan.append((kind, "rename", path, target, why))


def fix_dates(
    main_dir: Path,
    state_dir: Path,
    apply: bool = False,
    videos: bool = True,
    photos: bool = True,
    include_other_names: bool = False,
) -> int:
    state_dir.mkdir(parents=True, exist_ok=True)
    plan, skipped, taken = [], Counter(), {}

    if videos:
        print("Scanning videos...")
        vids = scan(main_dir, True, False)
        info = exiftool_json(vids, ["-Keys:CreationDate", "-QuickTime:CreateDate"], group=True)
        plan_files(main_dir, vids, info, "video", include_other_names, taken, plan, skipped)
    if photos:
        print("Scanning photos...")
        # Only camera-style (non-date) photos need metadata; date-named and mod_ ones are left alone.
        imgs = []
        for p in scan(main_dir, False, True):
            if p.name.startswith("mod_"):
                skipped["mod_ image"] += 1
            elif classify_name(p, include_other_names) == "date":
                skipped["photo already date-named"] += 1
            else:
                imgs.append(p)
        info = exiftool_json(imgs, ["-DateTimeOriginal", "-CreateDate"], group=False)
        plan_files(main_dir, imgs, info, "photo", include_other_names, taken, plan, skipped)

    renames = [p for p in plan if p[1] == "rename"]
    stamp_now = datetime.now().strftime("%Y%m%d-%H%M%S")
    report = state_dir / f"fix-dates-report-{stamp_now}.tsv"
    with open(report, "w") as f:
        f.write("kind\taction\told\tnew\treason\n")
        for kind, action, old, new, why in plan:
            f.write(
                f"{kind}\t{action}\t{old.relative_to(main_dir)}\t{new.relative_to(main_dir)}\t{why}\n"
            )

    print("\nWill rename:")
    for kind in ("video", "photo"):
        sel = [p for p in renames if p[0] == kind]
        if sel:
            print(f"  {kind}s: {len(sel)}  ({dict(Counter(p[4] for p in sel))})")
    print(f"  total: {len(renames)}")
    moved = sum(1 for p in renames if p[2].parent != p[3].parent)
    print(
        f"  of which move to a different month folder: {moved}; renamed in place: {len(renames) - moved}"
    )
    print("Not renamed:")
    for reason, n in skipped.most_common():
        print(f"  {n:>7}  {reason}")
    print("Samples:")
    for kind, _a, old, new, why in renames[:5] + renames[5:][-5:]:
        print(f"  {old.relative_to(main_dir)} -> {new.relative_to(main_dir)}  [{why}]")
    print(f"Full plan: {report}")

    if not apply:
        print("Report only. Re-run with --apply to rename.")
        return 0
    if not renames:
        return 0

    log = state_dir / f"fix-dates-{stamp_now}.tsv"
    done, changed = 0, []
    with open(log, "a") as f:
        for _kind, _a, old, new, _why in renames:
            # The plan may be stale (the scan can take a long time): never overwrite.
            if not old.exists() or new.exists():
                changed.append(old)
                continue
            new.parent.mkdir(parents=True, exist_ok=True)
            os.rename(old, new)
            f.write(f"{old}\t{new}\n")
            f.flush()
            done += 1
    print(f"Renamed {done} files. Undo log: {log}")
    if changed:
        print(
            f"Skipped {len(changed)} that changed since the scan (source gone or new name taken); "
            "run fix-dates again for them:"
        )
        for old in changed[:20]:
            print(f"  {old.relative_to(main_dir)}")
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
