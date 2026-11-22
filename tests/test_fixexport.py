"""fix_export_names, tested only through the public function.

Real files and a real SQLite export database under tmp_path; only the `osxphotos query --json`
call is faked. All names, dates and UUIDs are made up.
"""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from photo_backer_upper import fixdates, fixexport, library

# --- Fakes and helpers. Every patch is here, so a later step retargets it in one place. ---

# The module that runs `osxphotos query --json` (`library`).
LIBRARY_QUERY_MODULE = library


@pytest.fixture
def library(monkeypatch):
    """Stands in for `osxphotos query --json`. Tests add the items it should print."""
    lib = SimpleNamespace(items=[], calls=[])

    def fake_run(cmd, **kwargs):
        lib.calls.append(cmd)
        return SimpleNamespace(stdout=json.dumps(lib.items), stderr="", returncode=0)

    monkeypatch.setattr(LIBRARY_QUERY_MODULE.subprocess, "run", fake_run)
    return lib


def item(uuid, date, date_original=None):
    """One item as osxphotos prints it (only the fields fix-export-names reads)."""
    return {"uuid": uuid, "date": date, "date_original": date_original}


@pytest.fixture
def archive(tmp_path):
    path = tmp_path / "archive"
    path.mkdir()
    return path


@pytest.fixture
def state(tmp_path):
    path = tmp_path / "state"
    path.mkdir()
    return path


def make_export_db(state, rows):
    """osxphotos' export database with one export_data row per (uuid, archive-relative path)."""
    con = sqlite3.connect(state / "export.db")
    con.execute(
        "create table export_data (id integer primary key, filepath text, "
        "filepath_normalized text, uuid text)"
    )
    con.executemany(
        "insert into export_data (filepath, filepath_normalized, uuid) values (?,?,?)",
        [(path, path.lower(), uuid) for uuid, path in rows],
    )
    con.commit()
    con.close()


def add_file(archive, rel, data=b"photo"):
    path = archive / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def run_fix_export_names(archive, state, capsys, **kwargs):
    """Run fix_export_names; return what it printed and the rows of the report file it wrote."""
    assert fixexport.fix_export_names(archive, state, **kwargs) == 0
    out = capsys.readouterr().out
    (report,) = state.glob("fix-export-names-report-*.tsv")
    assert f"Plan written to {report}\n" in out
    return out, [tuple(line.split("\t")) for line in report.read_text().splitlines()]


def listing(archive):
    return sorted(str(p.relative_to(archive)) for p in archive.rglob("*") if p.is_file())


def undo_logs(state):
    return list(state.glob("fix-export-names-[0-9]*.tsv"))


# Made-up UUIDs.
U1 = "00000000-0000-4000-8000-000000000001"
U2 = "00000000-0000-4000-8000-000000000002"
U3 = "00000000-0000-4000-8000-000000000003"
U4 = "00000000-0000-4000-8000-000000000004"
U5 = "00000000-0000-4000-8000-000000000005"
U6 = "00000000-0000-4000-8000-000000000006"
U7 = "00000000-0000-4000-8000-000000000007"


def test_each_reason_a_file_is_not_renamed_is_counted(archive, state, library, capsys):
    library.items = [
        # already right: date and date_original share an offset, so the name doesn't change.
        # Counted before the file is looked for, so it needn't exist.
        item(U1, "2025-06-02T10:00:00-07:00", "2025-06-02T10:00:00-07:00"),
        # file missing: would be renamed, but isn't on disk.
        item(U2, "2025-06-03T17:00:00+00:00", "2025-06-03T10:00:00-07:00"),
        # unexpected name: not named from `date`.
        item(U3, "2025-06-04T17:00:00+00:00", "2025-06-04T10:00:00-07:00"),
        item(U4, "2025-06-05T17:00:00+00:00", "2025-06-05T10:00:00-07:00"),
        # no library item: no date (U5 is not in the library at all).
        item(U6, None, "2025-06-06T10:00:00-07:00"),
    ]
    make_export_db(
        state,
        [
            (U1, "2025-06/2025-06-02_10-00-00.heic"),
            (U2, "2025-06/2025-06-03_17-00-00.heic"),
            (U3, "2025-06/IMG_0001.heic"),
            (U4, "2025-06/2025-06-05_10-00-00.heic"),
            (U5, "2025-06/2025-06-06_17-00-00.heic"),
            (U6, "2025-06/2025-06-06_17-00-00.heic"),
        ],
    )
    add_file(archive, "2025-06/IMG_0001.heic")
    add_file(archive, "2025-06/2025-06-05_10-00-00.heic")
    add_file(archive, "2025-06/2025-06-06_17-00-00.heic")
    before = listing(archive)

    out, rows = run_fix_export_names(archive, state, capsys)

    (report,) = state.glob("fix-export-names-report-*.tsv")
    assert rows == []
    assert report.read_text() == ""
    assert out == (
        "Files recorded by earlier exports:      6\n"
        "  to rename (UTC name -> local time):   0 files for 0 items\n"
        "    of which move to another month:     0\n"
        "  already right:                         1\n"
        "  file missing:                          1\n"
        "  unexpected name:                       2\n"
        "  no library item:                       2\n"
        f"Plan written to {report}\n"
        "Report only; nothing renamed. Re-run with --apply.\n"
    )
    assert listing(archive) == before


def test_renames_utc_names_to_local_time_moving_month_if_needed(archive, state, library, capsys):
    library.items = [
        item(U1, "2025-06-01T17:00:00+00:00", "2025-06-01T10:00:00-07:00"),
        item(U2, "2025-07-01T03:00:00+00:00", "2025-06-30T20:00:00-07:00"),
    ]
    make_export_db(
        state,
        [(U1, "2025-06/2025-06-01_17-00-00.heic"), (U2, "2025-07/2025-07-01_03-00-00.heic")],
    )
    add_file(archive, "2025-06/2025-06-01_17-00-00.heic", b"first")
    add_file(archive, "2025-07/2025-07-01_03-00-00.heic", b"second")

    out, rows = run_fix_export_names(archive, state, capsys, apply=True)

    assert rows == [
        (U1, "2025-06/2025-06-01_17-00-00.heic", "2025-06/2025-06-01_10-00-00.heic"),
        (U2, "2025-07/2025-07-01_03-00-00.heic", "2025-06/2025-06-30_20-00-00.heic"),
    ]
    assert "  to rename (UTC name -> local time):   2 files for 2 items\n" in out
    assert "    of which move to another month:     1\n" in out
    assert listing(archive) == [
        "2025-06/2025-06-01_10-00-00.heic",
        "2025-06/2025-06-30_20-00-00.heic",
    ]
    assert (archive / "2025-06/2025-06-01_10-00-00.heic").read_bytes() == b"first"
    assert (archive / "2025-06/2025-06-30_20-00-00.heic").read_bytes() == b"second"


def test_queries_the_library_with_osxphotos(archive, state, library, capsys):
    make_export_db(state, [])
    run_fix_export_names(archive, state, capsys)
    for report in state.glob("fix-export-names-report-*.tsv"):
        report.unlink()
    run_fix_export_names(archive, state, capsys, library="/Users/example/Test.photoslibrary")
    assert library.calls == [
        ["osxphotos", "query", "--json"],
        ["osxphotos", "query", "--json", "--db", "/Users/example/Test.photoslibrary"],
    ]


def test_keeps_edited_and_numbered_suffixes_and_the_extension(archive, state, library, capsys):
    library.items = [
        item(U1, "2025-06-05T17:00:00+00:00", "2025-06-05T10:00:00-07:00"),
        item(U2, "2025-06-05T17:00:00+00:00", "2025-06-05T10:00:00-07:00"),
    ]
    old = [
        (U1, "2025-06/2025-06-05_17-00-00.heic"),
        (U1, "2025-06/2025-06-05_17-00-00_edited.heic"),
        (U1, "2025-06/2025-06-05_17-00-00.mov"),
        (U2, "2025-06/2025-06-05_17-00-00 (1).JPG"),
    ]
    make_export_db(state, old)
    for _uuid, rel in old:
        add_file(archive, rel)

    out, rows = run_fix_export_names(archive, state, capsys)

    assert rows == [
        (U1, "2025-06/2025-06-05_17-00-00.heic", "2025-06/2025-06-05_10-00-00.heic"),
        (U1, "2025-06/2025-06-05_17-00-00_edited.heic", "2025-06/2025-06-05_10-00-00_edited.heic"),
        (U1, "2025-06/2025-06-05_17-00-00.mov", "2025-06/2025-06-05_10-00-00.mov"),
        (U2, "2025-06/2025-06-05_17-00-00 (1).JPG", "2025-06/2025-06-05_10-00-00 (1).JPG"),
    ]
    assert "  to rename (UTC name -> local time):   4 files for 2 items\n" in out


def test_a_taken_new_name_gets_the_next_free_number(archive, state, library, capsys):
    library.items = [
        # The new name and its " (1)" are already on disk.
        item(U1, "2025-06-08T17:00:00+00:00", "2025-06-08T10:00:00-07:00"),
        # Two items whose new names are the same: the second one planned gets " (1)".
        item(U2, "2025-06-09T17:00:00+00:00", "2025-06-09T10:00:00-07:00"),
        item(U3, "2025-06-09T13:00:00-04:00", "2025-06-09T10:00:00-07:00"),
        # A " (N)" name whose new name is taken gets a second number.
        item(U4, "2025-06-10T17:00:00+00:00", "2025-06-10T10:00:00-07:00"),
    ]
    make_export_db(
        state,
        [
            (U1, "2025-06/2025-06-08_17-00-00.heic"),
            (U2, "2025-06/2025-06-09_17-00-00.heic"),
            (U3, "2025-06/2025-06-09_13-00-00.heic"),
            (U4, "2025-06/2025-06-10_17-00-00 (1).heic"),
        ],
    )
    for rel in (
        "2025-06/2025-06-08_17-00-00.heic",
        "2025-06/2025-06-08_10-00-00.heic",
        "2025-06/2025-06-08_10-00-00 (1).heic",
        "2025-06/2025-06-09_17-00-00.heic",
        "2025-06/2025-06-09_13-00-00.heic",
        "2025-06/2025-06-10_17-00-00 (1).heic",
        "2025-06/2025-06-10_10-00-00 (1).heic",
    ):
        add_file(archive, rel, rel.encode())

    _out, rows = run_fix_export_names(archive, state, capsys, apply=True)

    assert rows == [
        (U1, "2025-06/2025-06-08_17-00-00.heic", "2025-06/2025-06-08_10-00-00 (2).heic"),
        (U2, "2025-06/2025-06-09_17-00-00.heic", "2025-06/2025-06-09_10-00-00.heic"),
        (U3, "2025-06/2025-06-09_13-00-00.heic", "2025-06/2025-06-09_10-00-00 (1).heic"),
        (U4, "2025-06/2025-06-10_17-00-00 (1).heic", "2025-06/2025-06-10_10-00-00 (1) (1).heic"),
    ]
    # Nothing was overwritten: each file still holds its own old name.
    for _uuid, old, new in rows:
        assert (archive / new).read_bytes() == old.encode()
    for rel in ("2025-06/2025-06-08_10-00-00.heic", "2025-06/2025-06-08_10-00-00 (1).heic"):
        assert (archive / rel).read_bytes() == rel.encode()


def test_report_only_renames_nothing_and_leaves_the_skip_list_alone(
    archive, state, library, capsys
):
    library.items = [item(U1, "2025-06-01T17:00:00+00:00", "2025-06-01T10:00:00-07:00")]
    make_export_db(state, [(U1, "2025-06/2025-06-01_17-00-00.heic")])
    add_file(archive, "2025-06/2025-06-01_17-00-00.heic")
    skip = state / "skip-uuids.txt"
    skip.write_text(U7)

    out, rows = run_fix_export_names(archive, state, capsys)

    assert rows == [(U1, "2025-06/2025-06-01_17-00-00.heic", "2025-06/2025-06-01_10-00-00.heic")]
    assert out.endswith("Report only; nothing renamed. Re-run with --apply.\n")
    assert listing(archive) == ["2025-06/2025-06-01_17-00-00.heic"]
    assert skip.read_text() == U7
    assert undo_logs(state) == []


def test_report_only_does_not_create_a_skip_list(archive, state, library, capsys):
    library.items = [item(U1, "2025-06-01T17:00:00+00:00", "2025-06-01T10:00:00-07:00")]
    make_export_db(state, [(U1, "2025-06/2025-06-01_17-00-00.heic")])
    add_file(archive, "2025-06/2025-06-01_17-00-00.heic")
    run_fix_export_names(archive, state, capsys)
    assert not (state / "skip-uuids.txt").exists()


def test_apply_writes_an_undo_log_and_adds_new_uuids_to_the_skip_list(
    archive, state, library, capsys
):
    library.items = [
        item(U1, "2025-06-01T17:00:00+00:00", "2025-06-01T10:00:00-07:00"),
        item(U2, "2025-06-02T17:00:00+00:00", "2025-06-02T10:00:00-07:00"),
    ]
    make_export_db(
        state,
        [
            (U1, "2025-06/2025-06-01_17-00-00.heic"),
            (U2, "2025-06/2025-06-02_17-00-00.heic"),
            (U2, "2025-06/2025-06-02_17-00-00_edited.heic"),
        ],
    )
    add_file(archive, "2025-06/2025-06-01_17-00-00.heic")
    add_file(archive, "2025-06/2025-06-02_17-00-00.heic")
    add_file(archive, "2025-06/2025-06-02_17-00-00_edited.heic")
    before = listing(archive)
    skip = state / "skip-uuids.txt"
    skip.write_text(f"{U7}\n{U1}")  # U1 is already listed; no newline at the end

    out, _rows = run_fix_export_names(archive, state, capsys, apply=True)

    (log,) = undo_logs(state)
    assert out.endswith(
        f"Renamed 3 files. Undo log: {log}\n"
        "Added 1 items to the skip list so export leaves them alone.\n"
    )
    assert skip.read_text() == f"{U7}\n{U1}\n{U2}\n"
    assert listing(archive) == [
        "2025-06/2025-06-01_10-00-00.heic",
        "2025-06/2025-06-02_10-00-00.heic",
        "2025-06/2025-06-02_10-00-00_edited.heic",
    ]
    folder = archive / "2025-06"
    assert log.read_text(encoding="utf-8").splitlines() == [
        f"{folder}/2025-06-01_17-00-00.heic\t{folder}/2025-06-01_10-00-00.heic",
        f"{folder}/2025-06-02_17-00-00.heic\t{folder}/2025-06-02_10-00-00.heic",
        f"{folder}/2025-06-02_17-00-00_edited.heic\t{folder}/2025-06-02_10-00-00_edited.heic",
    ]

    # Same format as fix-dates, so `pback fix-dates --undo` reverses it.
    assert fixdates.undo(log) == 0
    assert listing(archive) == before


def test_apply_warns_when_a_renamed_file_does_not_check_out(archive, state, library, capsys):
    # The exported file is a relative symlink. Moved to another month folder it points at a file
    # of a different size there, so the size check after the rename fails.
    library.items = [item(U1, "2025-07-01T03:00:00+00:00", "2025-06-30T20:00:00-07:00")]
    make_export_db(state, [(U1, "2025-07/2025-07-01_03-00-00.heic")])
    add_file(archive, "2025-07/target.heic", b"exported")
    add_file(archive, "2025-06/target.heic", b"other")
    (archive / "2025-07/2025-07-01_03-00-00.heic").symlink_to("target.heic")

    out, rows = run_fix_export_names(archive, state, capsys, apply=True)

    dst = archive / "2025-06/2025-06-30_20-00-00.heic"
    assert rows == [(U1, "2025-07/2025-07-01_03-00-00.heic", "2025-06/2025-06-30_20-00-00.heic")]
    (log,) = undo_logs(state)
    assert out.endswith(
        f"Plan written to {next(state.glob('fix-export-names-report-*.tsv'))}\n"
        f"WARNING: check {dst}\n"
        f"Renamed 0 files. Undo log: {log}\n"
        "Added 1 items to the skip list so export leaves them alone.\n"
    )
    assert log.read_text() == ""
    assert (state / "skip-uuids.txt").read_text() == f"{U1}\n"
    assert dst.is_symlink()
    assert not (archive / "2025-07/2025-07-01_03-00-00.heic").exists()


def test_apply_with_nothing_to_rename_still_writes_undo_log_and_skip_list(
    archive, state, library, capsys
):
    make_export_db(state, [])
    out, rows = run_fix_export_names(archive, state, capsys, apply=True)
    (log,) = undo_logs(state)
    assert rows == []
    assert log.read_text() == ""
    assert (state / "skip-uuids.txt").read_text() == ""
    assert out.endswith(
        f"Renamed 0 files. Undo log: {log}\n"
        "Added 0 items to the skip list so export leaves them alone.\n"
    )


def test_no_export_database_means_nothing_to_do(archive, state, library, capsys):
    assert fixexport.fix_export_names(archive, state, apply=True) == 0
    assert capsys.readouterr().out == "No export database found; nothing to do.\n"
    assert library.calls == []
    assert list(state.iterdir()) == []
