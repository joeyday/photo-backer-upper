"""fix_dates, tested only through its public functions (`fix_dates` and `undo`).

Real files under tmp_path; only exiftool is faked. All names and dates are made up.
"""

import os

import pytest

from photo_backer_upper import fixdates
from photo_backer_upper.fixdates import scan

# --- Fakes and helpers. Every patch is here, so a later step retargets it in one place. ---


@pytest.fixture
def exif(monkeypatch):
    """Stands in for exiftool. Tests map a file's path to the tags exiftool would report."""
    rows = {}

    def fake_exiftool_json(paths, tags, group):
        return {p: rows[p] for p in paths if p in rows}

    monkeypatch.setattr(fixdates, "exiftool_json", fake_exiftool_json)
    return rows


@pytest.fixture
def archive(tmp_path):
    path = tmp_path / "archive"
    path.mkdir()
    return path


@pytest.fixture
def state(tmp_path):
    return tmp_path / "state"  # fix_dates creates it


def add_file(archive, rel, data):
    path = archive / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def add_video(archive, exif, rel, local=None, utc=None, data=b"video"):
    """A video; `local` is QuickTime Keys:CreationDate, `utc` is QuickTime:CreateDate."""
    path = add_file(archive, rel, data)
    tags = {"Keys:CreationDate": local, "QuickTime:CreateDate": utc}
    exif[path] = {k: v for k, v in tags.items() if v is not None}
    return path


def add_photo(archive, exif, rel, original=None, created=None, data=b"photo"):
    """A photo; `original` is DateTimeOriginal, `created` is CreateDate."""
    path = add_file(archive, rel, data)
    tags = {"DateTimeOriginal": original, "CreateDate": created}
    exif[path] = {k: v for k, v in tags.items() if v is not None}
    return path


def add_dangling_symlink(archive, rel):
    """A file os.walk lists but that can't be stat()ed."""
    path = archive / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(archive / "nowhere" / "gone")
    return path


def run_fix_dates(archive, state, capsys, **kwargs):
    """Run fix_dates; return what it printed and the rows of the report file it wrote."""
    assert fixdates.fix_dates(archive, state, **kwargs) == 0
    out = capsys.readouterr().out
    (report,) = state.glob("fix-dates-report-*.tsv")
    assert f"Full plan: {report}\n" in out
    header, *rows = report.read_text().splitlines()
    assert header == "kind\taction\told\tnew\treason"
    return out, [tuple(row.split("\t")) for row in rows]


def not_renamed(out):
    """The "Not renamed:" summary as {reason: count}."""
    section = out.split("Not renamed:\n")[1].split("Samples:\n")[0]
    counts = {}
    for line in section.splitlines():
        n, reason = line.strip().split("  ", 1)
        counts[reason] = int(n)
    return counts


def listing(archive):
    """Every file under the archive (symlinks included), relative and sorted."""
    return sorted(
        str(p.relative_to(archive)) for p in archive.rglob("*") if p.is_file() or p.is_symlink()
    )


def undo_logs(state):
    return list(state.glob("fix-dates-[0-9]*.tsv"))


def assert_left_alone(archive, state, capsys, skipped, **kwargs):
    """Even with apply=True nothing is renamed, the report is empty and `skipped` is counted."""
    before = listing(archive)
    out, rows = run_fix_dates(archive, state, capsys, apply=True, **kwargs)
    assert rows == []
    assert not_renamed(out) == skipped
    assert listing(archive) == before
    assert undo_logs(state) == []
    return out


# --- Skip reasons ---


def test_scan_skips_osxphotos_edited_copies(tmp_path):
    (tmp_path / "2025-06").mkdir()
    for n in (
        "2025-06-01_19-28-39_edited.heic",
        "2025-06-03_18-29-34_edited (1).mov",
        "2025-06-01_19-28-39.heic",
        "IMG_1234.JPG",
        ".hidden.jpg",
    ):
        (tmp_path / "2025-06" / n).write_bytes(b"x")
    assert [p.name for p in scan(tmp_path, True, True)] == [
        "2025-06-01_19-28-39.heic",
        "IMG_1234.JPG",
    ]


def test_edited_copies_and_hidden_files_are_not_even_counted(archive, state, exif, capsys):
    add_video(archive, exif, "2025-06/2025-06-01_17-00-00_edited.mov", local="2025-06-01_10-00-00")
    add_photo(
        archive, exif, "2025-06/2025-06-01_17-00-00_edited (1).heic", original="2025-06-01_10-00-00"
    )
    add_photo(archive, exif, "2025-06/.IMG_0001.jpg", original="2025-06-01_10-00-00")
    assert_left_alone(archive, state, capsys, {})


def test_skips_files_loose_at_the_archive_root(archive, state, exif, capsys):
    add_video(archive, exif, "MVI_0001.mov", local="2025-06-01_10-00-00")
    add_photo(archive, exif, "IMG_0002.jpg", original="2025-06-01_11-00-00")
    assert_left_alone(archive, state, capsys, {"loose at archive root (left alone)": 2})


def test_skips_a_mod_video_whose_name_is_not_a_date(archive, state, exif, capsys):
    add_video(archive, exif, "2025-06/mod_holiday.mov", local="2025-06-01_10-00-00")
    add_video(archive, exif, "2025-06/mod_MVI_0001.mov", local="2025-06-01_11-00-00")
    assert_left_alone(archive, state, capsys, {"unrecognized mod_ name": 2})


def test_skips_descriptive_names(archive, state, exif, capsys):
    add_video(archive, exif, "2025-06/Birthday cake.mov", local="2025-06-01_10-00-00")
    add_photo(archive, exif, "2025-06/Beach walk.jpg", original="2025-06-01_11-00-00")
    assert_left_alone(archive, state, capsys, {"descriptive name (left alone)": 2})


def test_skips_photos_already_named_with_a_date_even_if_it_is_wrong(archive, state, exif, capsys):
    add_photo(archive, exif, "2025-06/2025-06-01_10-00-00.jpg", original="2025-06-09_09-09-09")
    add_photo(archive, exif, "2025-06/2025-06-01_10-00-00 (1).heic", original="2025-06-09_09-09-09")
    add_photo(archive, exif, "2025-06/2025-06-01_10-00-00-2.png", original="2025-06-09_09-09-09")
    assert_left_alone(archive, state, capsys, {"photo already date-named": 3})


def test_skips_mod_images_whatever_their_name(archive, state, exif, capsys):
    add_photo(archive, exif, "2025-06/mod_2025-06-01_17-00-00.jpg", original="2025-06-01_10-00-00")
    add_photo(archive, exif, "2025-06/mod_sunset.jpg", original="2025-06-01_10-00-00")
    assert_left_alone(archive, state, capsys, {"mod_ image": 2})


def test_skips_videos_with_only_a_utc_date(archive, state, exif, capsys):
    add_video(archive, exif, "2025-06/MVI_0001.mov", utc="2025-06-01_17-00-00")
    add_video(
        archive,
        exif,
        "2025-06/MVI_0002.mov",
        local="0000:00:00 00:00:00",
        utc="2025-06-01_18-00-00",
    )
    assert_left_alone(archive, state, capsys, {"video: UTC time only (approximate)": 2})


def test_skips_files_with_no_usable_embedded_date(archive, state, exif, capsys):
    add_video(archive, exif, "2025-06/MVI_0001.mov")
    add_video(archive, exif, "2025-06/MVI_0002.mov", local="1985-06-01_10-00-00")  # before 1990
    add_photo(archive, exif, "2025-06/IMG_0003.jpg")
    # An unusable DateTimeOriginal is not replaced by a good CreateDate.
    add_photo(
        archive,
        exif,
        "2025-06/IMG_0004.jpg",
        original="0000:00:00 00:00:00",
        created="2025-06-01_10-00-00",
    )
    assert_left_alone(
        archive,
        state,
        capsys,
        {"video: no usable embedded date": 2, "photo: no usable embedded date": 2},
    )


def test_skips_videos_already_named_correctly_even_in_another_month_folder(
    archive, state, exif, capsys
):
    add_video(archive, exif, "2025-06/2025-06-01_10-00-00.mov", local="2025-06-01_10-00-00")
    add_video(archive, exif, "2025-06/2025-06-01_10-00-00 (1).mov", local="2025-06-01_10-00-00")
    add_video(archive, exif, "2025-06/2025-06-02_10-00-00-1.mp4", local="2025-06-02_10-00-00")
    add_video(archive, exif, "2025-07/2025-06-03_10-00-00.mov", local="2025-06-03_10-00-00")
    assert_left_alone(archive, state, capsys, {"video already correct": 4})


def test_skips_unreadable_files(archive, state, exif, capsys):
    exif[add_dangling_symlink(archive, "2025-06/MVI_0001.mov")] = {
        "Keys:CreationDate": "2025-06-01_10-00-00"
    }
    exif[add_dangling_symlink(archive, "2025-06/IMG_0002.jpg")] = {
        "DateTimeOriginal": "2025-06-01_11-00-00"
    }
    assert_left_alone(archive, state, capsys, {"video: unreadable": 1, "photo: unreadable": 1})


def test_skips_empty_files(archive, state, exif, capsys):
    add_video(archive, exif, "2025-06/MVI_0001.mov", local="2025-06-01_10-00-00", data=b"")
    add_photo(archive, exif, "2025-06/IMG_0002.jpg", original="2025-06-01_11-00-00", data=b"")
    assert_left_alone(archive, state, capsys, {"video: empty file": 1, "photo: empty file": 1})


def test_reports_probable_duplicates_instead_of_renaming(archive, state, exif, capsys):
    # Same size as a file already at the new name (after stepping past a different-sized one).
    add_file(archive, "2025-06/2025-06-02_08-00-00.jpg", b"a different size")
    add_file(archive, "2025-06/2025-06-02_08-00-00 (1).jpg", b"same")
    add_photo(archive, exif, "2025-06/IMG_0009.jpg", original="2025-06-02_08-00-00", data=b"same")
    # Same size as a file planned earlier in the same run.
    add_video(archive, exif, "2025-06/MVI_0010.mov", local="2025-06-03_09-00-00", data=b"clip")
    add_video(archive, exif, "2025-06/MVI_0011.mov", local="2025-06-03_09-00-00", data=b"clip")
    before = listing(archive)

    out, rows = run_fix_dates(archive, state, capsys)

    assert rows == [
        (
            "video",
            "rename",
            "2025-06/MVI_0010.mov",
            "2025-06/2025-06-03_09-00-00.mov",
            "camera name",
        ),
        ("video", "duplicate", "2025-06/MVI_0011.mov", "2025-06/2025-06-03_09-00-00.mov", ""),
        ("photo", "duplicate", "2025-06/IMG_0009.jpg", "2025-06/2025-06-02_08-00-00 (1).jpg", ""),
    ]
    assert not_renamed(out) == {
        "photo already date-named": 2,
        "probable duplicate of existing file (left alone)": 2,
    }
    assert "  total: 1\n" in out
    assert listing(archive) == before


# --- Rename reasons ---


def test_reason_utc_offset_for_a_whole_number_of_hours_up_to_14(archive, state, exif, capsys):
    add_video(archive, exif, "2025-06/2025-06-01_17-00-00.mov", local="2025-06-01_10-00-00")
    add_video(archive, exif, "2025-06/2025-06-02_00-00-00.mov", local="2025-06-02_14-00-00")
    _out, rows = run_fix_dates(archive, state, capsys)
    assert rows == [
        (
            "video",
            "rename",
            "2025-06/2025-06-01_17-00-00.mov",
            "2025-06/2025-06-01_10-00-00.mov",
            "UTC-offset",
        ),
        (
            "video",
            "rename",
            "2025-06/2025-06-02_00-00-00.mov",
            "2025-06/2025-06-02_14-00-00.mov",
            "UTC-offset",
        ),
    ]


def test_reason_small_drift_for_up_to_two_minutes(archive, state, exif, capsys):
    add_video(archive, exif, "2025-06/2025-06-03_10-00-30.mov", local="2025-06-03_10-00-00")
    add_video(archive, exif, "2025-06/2025-06-04_10-02-00.mov", local="2025-06-04_10-00-00")
    _out, rows = run_fix_dates(archive, state, capsys)
    assert [(old, new, why) for _k, _a, old, new, why in rows] == [
        ("2025-06/2025-06-03_10-00-30.mov", "2025-06/2025-06-03_10-00-00.mov", "small drift"),
        ("2025-06/2025-06-04_10-02-00.mov", "2025-06/2025-06-04_10-00-00.mov", "small drift"),
    ]


def test_reason_far_off_for_anything_else(archive, state, exif, capsys):
    add_video(archive, exif, "2025-06/2025-06-05_10-02-01.mov", local="2025-06-05_10-00-00")
    add_video(archive, exif, "2025-06/2025-06-06_01-00-00.mov", local="2025-06-06_16-00-00")
    add_video(archive, exif, "2025-06/2025-06-07_10-00-00.mov", local="2025-06-09_10-00-00")
    _out, rows = run_fix_dates(archive, state, capsys)
    assert [(old, new, why) for _k, _a, old, new, why in rows] == [
        ("2025-06/2025-06-05_10-02-01.mov", "2025-06/2025-06-05_10-00-00.mov", "far off"),
        ("2025-06/2025-06-06_01-00-00.mov", "2025-06/2025-06-06_16-00-00.mov", "far off"),
        ("2025-06/2025-06-07_10-00-00.mov", "2025-06/2025-06-09_10-00-00.mov", "far off"),
    ]


def test_reason_camera_name(archive, state, exif, capsys):
    add_video(archive, exif, "2025-06/MVI_0012.MOV", local="2025-06-10_11-00-00")
    add_photo(archive, exif, "2025-06/IMG_0011.JPG", original="2025-06-10_12-00-00")
    add_photo(archive, exif, "2025-06/DSC_0013.jpg", created="2025-06-10_13-00-00")
    _out, rows = run_fix_dates(archive, state, capsys)
    assert rows == [
        (
            "video",
            "rename",
            "2025-06/MVI_0012.MOV",
            "2025-06/2025-06-10_11-00-00.mov",
            "camera name",
        ),
        (
            "photo",
            "rename",
            "2025-06/DSC_0013.jpg",
            "2025-06/2025-06-10_13-00-00.jpg",
            "camera name",
        ),
        (
            "photo",
            "rename",
            "2025-06/IMG_0011.JPG",
            "2025-06/2025-06-10_12-00-00.jpg",
            "camera name",
        ),
    ]


def test_mod_video_loses_the_prefix_and_its_reason_comes_from_the_date_difference(
    archive, state, exif, capsys
):
    # The "mod_" reason in fixdates.py is never produced today: a mod_ video either has a date
    # name (reason from the time difference, even when there is none) or is skipped.
    add_video(archive, exif, "2025-06/mod_2025-06-11_10-00-00.mov", local="2025-06-11_10-00-00")
    add_video(archive, exif, "2025-06/mod_2025-06-12_17-00-00.mov", local="2025-06-12_10-00-00")
    _out, rows = run_fix_dates(archive, state, capsys)
    assert rows == [
        (
            "video",
            "rename",
            "2025-06/mod_2025-06-11_10-00-00.mov",
            "2025-06/2025-06-11_10-00-00.mov",
            "small drift",
        ),
        (
            "video",
            "rename",
            "2025-06/mod_2025-06-12_17-00-00.mov",
            "2025-06/2025-06-12_10-00-00.mov",
            "UTC-offset",
        ),
    ]


# --- Other behavior ---


def test_only_files_in_a_yyyy_mm_folder_move_to_their_corrected_month(archive, state, exif, capsys):
    add_photo(archive, exif, "2025-06/IMG_0020.jpg", original="2025-07-04_09-00-00")
    add_photo(archive, exif, "Old scans/2019-03/IMG_0022.jpg", original="2019-04-01_12-00-00")
    add_photo(archive, exif, "Trips/2025-06 Beach/IMG_0021.jpg", original="2025-07-05_09-00-00")

    out, rows = run_fix_dates(archive, state, capsys, apply=True)

    assert [(old, new) for _k, _a, old, new, _why in rows] == [
        ("2025-06/IMG_0020.jpg", "2025-07/2025-07-04_09-00-00.jpg"),
        ("Old scans/2019-03/IMG_0022.jpg", "Old scans/2019-04/2019-04-01_12-00-00.jpg"),
        ("Trips/2025-06 Beach/IMG_0021.jpg", "Trips/2025-06 Beach/2025-07-05_09-00-00.jpg"),
    ]
    assert "  of which move to a different month folder: 2; renamed in place: 1\n" in out
    assert listing(archive) == [
        "2025-07/2025-07-04_09-00-00.jpg",
        "Old scans/2019-04/2019-04-01_12-00-00.jpg",
        "Trips/2025-06 Beach/2025-07-05_09-00-00.jpg",
    ]


def test_clashing_new_names_get_a_numbered_suffix(archive, state, exif, capsys):
    add_photo(archive, exif, "2025-06/IMG_0030.jpg", original="2025-06-20_08-00-00", data=b"1")
    add_photo(archive, exif, "2025-06/IMG_0031.jpg", original="2025-06-20_08-00-00", data=b"22")
    add_photo(archive, exif, "2025-06/IMG_0032.jpg", original="2025-06-20_08-00-00", data=b"333")
    add_file(archive, "2025-06/2025-06-21_08-00-00.jpg", b"already here")
    add_photo(archive, exif, "2025-06/IMG_0033.jpg", original="2025-06-21_08-00-00", data=b"4444")

    _out, rows = run_fix_dates(archive, state, capsys, apply=True)

    assert [(old, new) for _k, _a, old, new, _why in rows] == [
        ("2025-06/IMG_0030.jpg", "2025-06/2025-06-20_08-00-00.jpg"),
        ("2025-06/IMG_0031.jpg", "2025-06/2025-06-20_08-00-00 (1).jpg"),
        ("2025-06/IMG_0032.jpg", "2025-06/2025-06-20_08-00-00 (2).jpg"),
        ("2025-06/IMG_0033.jpg", "2025-06/2025-06-21_08-00-00 (1).jpg"),
    ]
    folder = archive / "2025-06"
    assert (folder / "2025-06-20_08-00-00.jpg").read_bytes() == b"1"
    assert (folder / "2025-06-20_08-00-00 (1).jpg").read_bytes() == b"22"
    assert (folder / "2025-06-20_08-00-00 (2).jpg").read_bytes() == b"333"
    assert (folder / "2025-06-21_08-00-00.jpg").read_bytes() == b"already here"
    assert (folder / "2025-06-21_08-00-00 (1).jpg").read_bytes() == b"4444"


def test_include_other_names_renames_descriptive_names(archive, state, exif, capsys):
    add_video(archive, exif, "2025-06/Birthday cake.mov", local="2025-06-01_10-00-00")
    add_photo(archive, exif, "2025-06/Beach walk.jpg", original="2025-06-01_11-00-00")
    add_video(archive, exif, "2025-06/mod_holiday.mov", local="2025-06-01_12-00-00")

    out, rows = run_fix_dates(archive, state, capsys, include_other_names=True)

    assert rows == [
        (
            "video",
            "rename",
            "2025-06/Birthday cake.mov",
            "2025-06/2025-06-01_10-00-00.mov",
            "camera name",
        ),
        (
            "photo",
            "rename",
            "2025-06/Beach walk.jpg",
            "2025-06/2025-06-01_11-00-00.jpg",
            "camera name",
        ),
    ]
    assert not_renamed(out) == {"unrecognized mod_ name": 1}


def test_report_only_renames_nothing_and_writes_the_full_plan(archive, state, exif, capsys):
    add_video(archive, exif, "2025-06/2025-06-01_17-00-00.mov", local="2025-06-01_10-00-00")
    add_video(archive, exif, "2025-06/Fireworks.mov", local="2025-06-01_21-00-00")
    add_video(archive, exif, "2025-06/MVI_0001.mov", local="2025-07-02_10-00-00")
    for i in range(1, 11):
        add_photo(archive, exif, f"2025-06/IMG_{i:04}.jpg", original=f"2025-06-03_08-00-{i:02}")
    add_photo(archive, exif, "2025-06/2025-06-04_10-00-00.jpg")
    add_photo(archive, exif, "2025-06/2025-06-05_10-00-00.jpg")
    add_photo(archive, exif, "2025-06/mod_2025-06-06_10-00-00.jpg")
    before = listing(archive)

    out, rows = run_fix_dates(archive, state, capsys)

    assert listing(archive) == before
    assert undo_logs(state) == []
    photo_rows = [
        (
            "photo",
            "rename",
            f"2025-06/IMG_{i:04}.jpg",
            f"2025-06/2025-06-03_08-00-{i:02}.jpg",
            "camera name",
        )
        for i in range(1, 11)
    ]
    assert rows == [
        (
            "video",
            "rename",
            "2025-06/2025-06-01_17-00-00.mov",
            "2025-06/2025-06-01_10-00-00.mov",
            "UTC-offset",
        ),
        (
            "video",
            "rename",
            "2025-06/MVI_0001.mov",
            "2025-07/2025-07-02_10-00-00.mov",
            "camera name",
        ),
        *photo_rows,
    ]
    (report,) = state.glob("fix-dates-report-*.tsv")
    samples = [1, 2, 3, 6, 7, 8, 9, 10]
    assert out == (
        "Scanning videos...\n"
        "Scanning photos...\n"
        "\n"
        "Will rename:\n"
        "  videos: 2  ({'UTC-offset': 1, 'camera name': 1})\n"
        "  photos: 10  ({'camera name': 10})\n"
        "  total: 12\n"
        "  of which move to a different month folder: 1; renamed in place: 11\n"
        "Not renamed:\n"
        "        2  photo already date-named\n"
        "        1  descriptive name (left alone)\n"
        "        1  mod_ image\n"
        "Samples:\n"
        "  2025-06/2025-06-01_17-00-00.mov -> 2025-06/2025-06-01_10-00-00.mov  [UTC-offset]\n"
        "  2025-06/MVI_0001.mov -> 2025-07/2025-07-02_10-00-00.mov  [camera name]\n"
        + "".join(
            f"  2025-06/IMG_{i:04}.jpg -> 2025-06/2025-06-03_08-00-{i:02}.jpg  [camera name]\n"
            for i in samples
        )
        + f"Full plan: {report}\n"
        "Report only. Re-run with --apply to rename.\n"
    )


def test_apply_writes_an_undo_log_that_undo_reverses(archive, state, exif, capsys):
    add_video(archive, exif, "2025-06/2025-06-01_17-00-00.mov", local="2025-06-01_10-00-00")
    add_photo(archive, exif, "2025-06/IMG_0001.jpg", original="2025-07-01_09-00-00")
    before = listing(archive)

    out, _rows = run_fix_dates(archive, state, capsys, apply=True)

    (log,) = undo_logs(state)
    assert out.endswith(f"Renamed 2 files. Undo log: {log}\n")
    assert listing(archive) == [
        "2025-06/2025-06-01_10-00-00.mov",
        "2025-07/2025-07-01_09-00-00.jpg",
    ]
    assert log.read_text().splitlines() == [
        f"{archive}/2025-06/2025-06-01_17-00-00.mov\t{archive}/2025-06/2025-06-01_10-00-00.mov",
        f"{archive}/2025-06/IMG_0001.jpg\t{archive}/2025-07/2025-07-01_09-00-00.jpg",
    ]

    assert fixdates.undo(log) == 0
    assert capsys.readouterr().out == f"Restored 2 files from {log}\n"
    assert listing(archive) == before


def test_apply_with_nothing_to_rename_writes_no_undo_log(archive, state, exif, capsys):
    add_photo(archive, exif, "2025-06/2025-06-01_10-00-00.jpg")
    out, _rows = run_fix_dates(archive, state, capsys, apply=True)
    assert out.endswith(f"Full plan: {next(state.glob('fix-dates-report-*.tsv'))}\n")
    assert undo_logs(state) == []


def test_apply_never_overwrites_a_name_taken_after_planning(archive, state, exif, capsys):
    # IMG_0002's new name is a dangling symlink to IMG_0001's new name: free at planning, but
    # taken once IMG_0001 has been renamed earlier in the same apply.
    add_photo(archive, exif, "2025-06/IMG_0001.jpg", original="2025-06-02_08-00-00", data=b"one")
    add_photo(archive, exif, "2025-06/IMG_0002.jpg", original="2025-06-03_09-00-00", data=b"two")
    clash = archive / "2025-06" / "2025-06-03_09-00-00.jpg"
    clash.symlink_to("2025-06-02_08-00-00.jpg")

    out, rows = run_fix_dates(archive, state, capsys, apply=True)

    assert [(old, new) for _k, _a, old, new, _why in rows] == [
        ("2025-06/IMG_0001.jpg", "2025-06/2025-06-02_08-00-00.jpg"),
        ("2025-06/IMG_0002.jpg", "2025-06/2025-06-03_09-00-00.jpg"),
    ]
    (log,) = undo_logs(state)
    assert out.endswith(
        f"Renamed 1 files. Undo log: {log}\n"
        "Skipped 1 that changed since the scan (source gone or new name taken); "
        "run fix-dates again for them:\n"
        "  2025-06/IMG_0002.jpg\n"
    )
    assert (archive / "2025-06" / "IMG_0002.jpg").read_bytes() == b"two"
    assert os.readlink(clash) == "2025-06-02_08-00-00.jpg"
    assert clash.read_bytes() == b"one"


def test_videos_only_and_photos_only(archive, state, exif, capsys):
    add_video(archive, exif, "2025-06/MVI_0001.mov", local="2025-06-01_10-00-00")
    add_photo(archive, exif, "2025-06/IMG_0002.jpg", original="2025-06-01_11-00-00")

    out, rows = run_fix_dates(archive, state, capsys, photos=False)
    assert out.startswith("Scanning videos...\n\nWill rename:\n")
    assert [old for _k, _a, old, _n, _w in rows] == ["2025-06/MVI_0001.mov"]

    for report in state.glob("fix-dates-report-*.tsv"):
        report.unlink()
    out, rows = run_fix_dates(archive, state, capsys, videos=False)
    assert out.startswith("Scanning photos...\n\nWill rename:\n")
    assert [old for _k, _a, old, _n, _w in rows] == ["2025-06/IMG_0002.jpg"]


def test_samples_list_each_rename_once_when_there_are_fewer_than_ten(archive, state, exif, capsys):
    for i in range(1, 8):
        add_photo(archive, exif, f"2025-06/IMG_{i:04}.jpg", original=f"2025-06-03_08-00-{i:02}")
    out, _rows = run_fix_dates(archive, state, capsys)
    samples = out.split("Samples:\n")[1].split("Full plan:")[0]
    assert samples == "".join(
        f"  2025-06/IMG_{i:04}.jpg -> 2025-06/2025-06-03_08-00-{i:02}.jpg  [camera name]\n"
        for i in range(1, 8)
    )
