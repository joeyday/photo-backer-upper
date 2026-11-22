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

STEM_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})(?:-\d+| \(\d+\))?$")
STEM_FMT = "%Y-%m-%d_%H-%M-%S"
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


def load_library(library: str = None) -> list:
    cmd = ["osxphotos", "query", "--json"]
    if library:
        cmd += ["--db", library]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    photos = json.loads(out)
    # Same filter as `export`: ignore items merely "Shared with You" that weren't saved to the library.
    return [p for p in photos if not (p.get("syndicated") and not p.get("saved_to_library"))]


def candidate_stems(photo: dict) -> list:
    """Possible archive stems for a photo, best first.

    `date_original` is the original EXIF capture time in local time, which is what the old
    exiftool-based names used. `date` can differ (stored as UTC for some items, or the time
    after an edit), and some archive files were named from it, so it's a fallback.
    """
    stems = []
    for key in ("date_original", "date"):
        if photo.get(key):
            s = datetime.fromisoformat(photo[key]).strftime(STEM_FMT)
            if s not in stems:
                stems.append(s)
    return stems


def read_archive_metadata(paths: list) -> dict:
    """Read the comparison tags for archive files with exiftool, in batches."""
    meta = {}
    for i in range(0, len(paths), BATCH):
        chunk = paths[i : i + BATCH]
        with tempfile.NamedTemporaryFile("w", suffix=".args", encoding="utf-8") as af:
            af.write("".join(f"{p}\n" for p in chunk))
            af.flush()
            out = subprocess.run(
                ["exiftool", "-j", "-n", "-charset", "filename=utf8", *EXIF_TAGS, "-@", af.name],
                capture_output=True,
                text=True,
            ).stdout
        for d in json.loads(out or "[]"):
            meta[Path(d["SourceFile"])] = d
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

    lw, lh = photo.get("original_width"), photo.get("original_height")
    aw, ah = meta.get("ImageWidth"), meta.get("ImageHeight")
    if lw and lh and aw and ah:
        r1, r2 = max(lw, lh) / min(lw, lh), max(aw, ah) / min(aw, ah)
        (agree if abs(r1 - r2) / r1 < 0.02 else differ).add("aspect")

    if photo.get("ismovie"):
        ld, ad = photo.get("duration"), meta.get("Duration")
        if ld and ad:
            (agree if abs(ld - ad) <= max(1.5, 0.03 * ld) else differ).add("duration")
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
    for p in photos:
        seen, files = set(), []
        for s in candidate_stems(p):
            for f in archive.get(s, []):
                if f not in seen:
                    seen.add(f)
                    files.append(f)
        candidates[p["uuid"]] = files

    needed = sorted({f for files in candidates.values() for f in files})
    print(
        f"Comparing {len(photos)} library items against {len(needed)} name-matched archive files...",
        flush=True,
    )
    meta = read_archive_metadata(needed)

    # Each archive file can vouch for only one library item. Items with the fewest
    # candidates go first so bursts and duplicates sort out predictably.
    claimed, skip, unconfirmed, not_in_archive = set(), [], [], []
    for p in sorted(photos, key=lambda p: (len(candidates[p["uuid"]]), p["uuid"])):
        files = candidates[p["uuid"]]
        if not files:
            not_in_archive.append(p)
            continue
        match = next(
            (f for f in files if f not in claimed and same_photo(p, meta.get(f, {}))), None
        )
        if match:
            claimed.add(match)
            skip.append(p)
        else:
            unconfirmed.append((p, files))

    videos_new = [p for p in not_in_archive if p.get("ismovie")]
    print(f"Archive files with date-stamped names: {sum(len(v) for v in archive.values())}")
    print(f"Library items:                         {len(photos)}")
    print(f"  already in archive (will be skipped): {len(skip)}")
    print(f"  not in archive (will be exported):    {len(not_in_archive)}")
    print(f"    of which videos:                    {len(videos_new)}")
    print(f"  name matches but not confirmed (will be exported; may duplicate): {len(unconfirmed)}")

    report = state_dir / UNCONFIRMED_FILE
    if dry_run:
        for p, files in unconfirmed[:5]:
            print(
                f"    e.g. {p.get('original_filename')} {p.get('date_original')} vs {files[0].name}"
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
