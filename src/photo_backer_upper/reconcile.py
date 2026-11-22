"""One-time reconcile: find library photos that are already in the archive.

Read-only on the archive. A library item is skipped only if an archive file both
(1) carries its capture-time filename (YYYY-MM-DD_HH-MM-SS, optional "-N" or " (N)" suffix,
any extension, which is how the old Hazel rules named files) and
(2) looks like the same photo by its metadata: camera model, sub-second capture time,
aspect ratio, and for videos the duration. A name match alone isn't enough, because the
archive holds several people's photos and two can share a capture second.
Anything that doesn't check out is exported instead, which can at worst create a duplicate
(with a " (1)" suffix), never a missing photo. Writes a UUID skip list that `export`
passes to osxphotos. Nothing in the archive is modified, replaced, or deleted.
"""

import json
import os
import re
import subprocess
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .atomic import write_text_atomic
from .library import load_library
from .naming import TIMESTAMP_FMT, TIMESTAMP_PATTERN

STEM_RE = re.compile("^(" + TIMESTAMP_PATTERN + r")(?:-\d+| \(\d+\))?$")
SKIP_FILE = "skip-uuids.txt"
UNCONFIRMED_FILE = "reconcile-unconfirmed.tsv"
EXIF_TAGS = ["-SubSecDateTimeOriginal", "-Model", "-ImageWidth", "-ImageHeight", "-Duration"]
BATCH = 2000


def index_archive(main_dir: Path) -> dict:
    """Map each date stem to the archive files carrying it. Other names (e.g. mod_*) are ignored."""
    stems = defaultdict(list)
    for root, _dirs, files in os.walk(main_dir):
        for name in files:
            if name.startswith("."):
                continue
            m = STEM_RE.match(Path(name).stem)
            if m:
                stems[m.group(1)].append(Path(root) / name)
    return stems


def candidate_stems(photo: dict) -> list:
    """Possible archive stems for a photo, best first.

    `date_original` is the original EXIF capture time in local time, which is what the old
    exiftool-based names used. `date` can differ (stored as UTC for some items, or the time
    after an edit), and some archive files were named from it, so it's a fallback.
    """
    stems = []
    for key in ("date_original", "date"):
        if photo.get(key):
            stem = datetime.fromisoformat(photo[key]).strftime(TIMESTAMP_FMT)
            if stem not in stems:
                stems.append(stem)
    return stems


def read_archive_metadata(paths: list) -> dict:
    """Read the comparison tags for archive files with exiftool, in batches."""
    meta = {}
    for i in range(0, len(paths), BATCH):
        chunk = paths[i : i + BATCH]
        with tempfile.NamedTemporaryFile("w", suffix=".args", encoding="utf-8") as args_file:
            args_file.write("".join(f"{p}\n" for p in chunk))
            args_file.flush()
            out = subprocess.run(
                [
                    "exiftool",
                    "-j",
                    "-n",
                    "-charset",
                    "filename=utf8",
                    *EXIF_TAGS,
                    "-@",
                    args_file.name,
                ],
                capture_output=True,
                text=True,
            ).stdout
        for row in json.loads(out or "[]"):
            meta[Path(row["SourceFile"])] = row
        print(
            f"  read metadata for {min(i + BATCH, len(paths))}/{len(paths)} archive files",
            flush=True,
        )
    return meta


def _fraction(value) -> str:
    """First two digits of the sub-second part of a timestamp, or None."""
    m = re.search(r"\d{2}:\d{2}:\d{2}\.(\d+)", str(value or ""))
    return m.group(1)[:2] if m else None


def compare(photo: dict, meta: dict):
    """(agreeing, contradicting) attribute names between a library item and an archive file."""
    agree, differ = set(), set()
    lib_model = ((photo.get("exif_info") or {}).get("camera_model") or "").strip().lower()
    arc_model = str(meta.get("Model") or "").strip().lower()
    if lib_model and arc_model:
        (agree if lib_model == arc_model else differ).add("model")

    lib_frac, arc_frac = (
        _fraction(photo.get("date_original")),
        _fraction(meta.get("SubSecDateTimeOriginal")),
    )
    if lib_frac and arc_frac and not (lib_frac == "00" and arc_frac == "00"):
        (agree if lib_frac == arc_frac else differ).add("subsec")

    library_width, library_height = photo.get("original_width"), photo.get("original_height")
    archive_width, archive_height = meta.get("ImageWidth"), meta.get("ImageHeight")
    if library_width and library_height and archive_width and archive_height:
        library_ratio, archive_ratio = (
            max(library_width, library_height) / min(library_width, library_height),
            max(archive_width, archive_height) / min(archive_width, archive_height),
        )
        (agree if abs(library_ratio - archive_ratio) / library_ratio < 0.02 else differ).add(
            "aspect"
        )

    if photo.get("ismovie"):
        library_duration, archive_duration = photo.get("duration"), meta.get("Duration")
        if library_duration and archive_duration:
            (
                agree
                if abs(library_duration - archive_duration) <= max(1.5, 0.03 * library_duration)
                else differ
            ).add("duration")
    return agree, differ


def same_photo(photo: dict, meta: dict) -> bool:
    """True if the archive file is evidently the same photo as the library item.

    A different camera, sub-second time or video duration rules it out. A different aspect
    ratio is tolerated only when the camera or sub-second time agree (a cropped or edited
    copy). With nothing at all to compare, it is not treated as the same.
    """
    agree, differ = compare(photo, meta)
    if differ & {"model", "subsec", "duration"}:
        return False
    if "aspect" in differ:
        return bool(agree & {"model", "subsec"})
    return bool(agree)


def reconcile(main_dir: Path, state_dir: Path, library: str = None, dry_run: bool = False) -> int:
    print("Indexing the archive...", flush=True)
    archive = index_archive(main_dir)
    print("Reading the Photos library...", flush=True)
    photos = load_library(library)

    candidates = {}
    for photo in photos:
        seen, files = set(), []
        for stem in candidate_stems(photo):
            for archive_file in archive.get(stem, []):
                if archive_file not in seen:
                    seen.add(archive_file)
                    files.append(archive_file)
        candidates[photo["uuid"]] = files

    needed = sorted({f for files in candidates.values() for f in files})
    print(
        f"Comparing {len(photos)} library items against {len(needed)} name-matched "
        "archive files...",
        flush=True,
    )
    meta = read_archive_metadata(needed)

    # Each archive file can vouch for only one library item. Items with the fewest
    # candidates go first so bursts and duplicates sort out predictably.
    claimed, skip, unconfirmed, not_in_archive = set(), [], [], []
    for photo in sorted(photos, key=lambda p: (len(candidates[p["uuid"]]), p["uuid"])):
        files = candidates[photo["uuid"]]
        if not files:
            not_in_archive.append(photo)
            continue
        match = next(
            (f for f in files if f not in claimed and same_photo(photo, meta.get(f, {}))), None
        )
        if match:
            claimed.add(match)
            skip.append(photo)
        else:
            unconfirmed.append((photo, files))

    videos_new = [p for p in not_in_archive if p.get("ismovie")]
    print(f"Archive files with date-stamped names: {sum(len(v) for v in archive.values())}")
    print(f"Library items:                         {len(photos)}")
    print(f"  already in archive (will be skipped): {len(skip)}")
    print(f"  not in archive (will be exported):    {len(not_in_archive)}")
    print(f"    of which videos:                    {len(videos_new)}")
    print(f"  name matches but not confirmed (will be exported; may duplicate): {len(unconfirmed)}")

    report = state_dir / UNCONFIRMED_FILE
    if dry_run:
        for photo, files in unconfirmed[:5]:
            print(
                f"    e.g. {photo.get('original_filename')} {photo.get('date_original')} "
                f"vs {files[0].name}"
            )
        print("Dry run: skip list and report not written.")
        return 0
    state_dir.mkdir(parents=True, exist_ok=True)
    write_text_atomic(
        report,
        "".join(
            f"{p['uuid']}\t{p.get('original_filename')}\t{p.get('date_original')}\t"
            f"{';'.join(str(f.relative_to(main_dir)) for f in files)}\n"
            for p, files in unconfirmed
        ),
    )
    path = state_dir / SKIP_FILE
    write_text_atomic(path, "".join(f"{p['uuid']}\n" for p in skip))
    print(f"Wrote {len(skip)} UUIDs to {path}")
    print(f"Unconfirmed name matches listed in {report}")
    return 0
