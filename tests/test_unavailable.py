import os
import sqlite3
from pathlib import Path

from photo_backer_upper import cli
from photo_backer_upper.unavailable import (
    LIVE_UNAVAILABLE_FILE,
    RETRY_DAYS,
    load_live_unavailable,
    missing_live_uuids,
    remember_live_unavailable,
    write_merged_skip,
)


def make_db(path, history, records):
    con = sqlite3.connect(path)
    con.executescript("""
        create table history_path (id integer primary key, filepath_normalized text, uuid text);
        create table history (id integer primary key, filepath_id integer, datetime text,
                              action text, diff text);
        create table export_data (id integer primary key, filepath_normalized text, uuid text);
    """)
    for i, (fp, uuid, when, action) in enumerate(history, 1):
        con.execute("insert into history_path values (?,?,?)", (i, fp, uuid))
        con.execute("insert into history values (?,?,?,?,null)", (i, i, when, action))
    con.executemany("insert into export_data (filepath_normalized, uuid) values (?,?)", records)
    con.commit()
    con.close()


def test_missing_live_uuids_only_live_photos_from_this_run(tmp_path):
    db = tmp_path / "export.db"
    make_db(
        db,
        history=[
            ("2026-11/a_edited.mov", "LIVE", "2026-11-21 18:00:01.000", "missing"),
            (
                "2026-11/o.mov",
                "ORIGINAL",
                "2026-11-21 18:00:01.500",
                "missing",
            ),  # original live video
            ("2026-11/v_edited.mov", "VIDEO", "2026-11-21 18:00:02.000", "missing"),  # plain video
            ("2026-11/b_edited.mov", "OLD", "2026-11-20 18:00:00.000", "missing"),  # earlier run
            ("2026-11/c_edited.mov", "SKIPPED", "2026-11-21 18:00:03.000", "skip"),
            ("2026-11/d.heic", "STILL", "2026-11-21 18:00:04.000", "missing"),
        ],
        records=[
            ("2026-11/a.heic", "LIVE"),
            ("2026-11/o.heic", "ORIGINAL"),
            ("2026-11/b.heic", "OLD"),
            ("2026-11/c.heic", "SKIPPED"),
            ("2026-11/x.mov", "VIDEO"),
        ],
    )
    assert missing_live_uuids(db, "2026-11-21 18:00:00") == {"LIVE"}


def test_remember_appends_only_new_and_load_reads_back(tmp_path):
    assert remember_live_unavailable(tmp_path, {"B", "A"}) == ["A", "B"]
    assert remember_live_unavailable(tmp_path, {"A", "C"}) == ["C"]
    assert load_live_unavailable(tmp_path) == ["A", "B", "C"]


def test_list_expires_for_retry(tmp_path):
    remember_live_unavailable(tmp_path, {"A"})
    path = tmp_path / LIVE_UNAVAILABLE_FILE
    old = path.stat().st_mtime - (RETRY_DAYS + 1) * 86400
    os.utime(path, (old, old))
    assert load_live_unavailable(tmp_path) == []
    assert not path.exists()


def test_merged_skip_dedupes(tmp_path):
    merged = write_merged_skip(tmp_path, ["A", "B"], ["B", "C"])
    assert merged.read_text().split() == ["A", "B", "C"]


def test_cmd_export_runs_second_pass_with_skip_live(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    archive.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (state / "skip-uuids.txt").write_text("S1\n")
    remember_live_unavailable(state, {"L1"})
    calls = []
    monkeypatch.setattr(cli, "main_archive", lambda: archive)
    monkeypatch.setattr(cli, "state_dir", lambda: state)
    monkeypatch.setattr(cli, "require_tool", lambda *a: None)
    monkeypatch.setattr(cli, "run_cmd", lambda cmd: calls.append(cmd) or 0)
    monkeypatch.setattr(cli.config, "get", lambda *a, **k: None)

    assert cli.cmd_export(cli.build_parser().parse_args(["export"])) == 0

    main, live = calls
    merged = main[main.index("--skip-uuid-from-file") + 1]
    assert Path(merged).read_text().split() == ["S1", "L1"]
    assert "--skip-live" not in main
    assert live[live.index("--skip-uuid-from-file") + 1] == str(state / "skip-uuids.txt")
    assert live[live.index("--uuid-from-file") + 1] == str(state / LIVE_UNAVAILABLE_FILE)
    assert "--skip-live" in live


def test_cmd_export_fails_on_file_errors(tmp_path, monkeypatch, capsys):
    archive = tmp_path / "archive"
    archive.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (state / "skip-uuids.txt").write_text("")
    (state / cli.EXPORT_REPORT_FILE).write_text("stale report from an earlier run")

    def fake_osxphotos(cmd):
        report = cmd[cmd.index("--report") + 1]
        con = sqlite3.connect(report)
        con.execute("create table report (filename text, error text)")
        con.executemany(
            "insert into report values (?,?)",
            [("/a/2025-01/ok.heic", ""), ("/a/2025-01/bad.heic", "disk full")],
        )
        con.commit()
        con.close()
        return 0  # osxphotos exits 0 even with file errors

    monkeypatch.setattr(cli, "main_archive", lambda: archive)
    monkeypatch.setattr(cli, "state_dir", lambda: state)
    monkeypatch.setattr(cli, "require_tool", lambda *a: None)
    monkeypatch.setattr(cli, "run_cmd", fake_osxphotos)
    monkeypatch.setattr(cli.config, "get", lambda *a, **k: None)

    assert cli.cmd_export(cli.build_parser().parse_args(["export"])) == 1
    out = capsys.readouterr().out
    assert "EXPORT ERRORS: 1 file(s)" in out
    assert "/a/2025-01/bad.heic: disk full" in out
    assert "ok.heic" not in out


def test_cmd_export_reports_missing(tmp_path, monkeypatch, capsys):

    archive = tmp_path / "archive"
    archive.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (state / "skip-uuids.txt").write_text("")
    notes = []
    monkeypatch.setattr(cli, "main_archive", lambda: archive)
    monkeypatch.setattr(cli, "state_dir", lambda: state)
    monkeypatch.setattr(cli, "require_tool", lambda *a: None)
    monkeypatch.setattr(cli, "run_cmd", lambda cmd: 0)
    monkeypatch.setattr(cli.config, "get", lambda *a, **k: None)
    monkeypatch.setattr(cli, "missing_this_run", lambda db, since: ["2025-01/a.mov"])
    monkeypatch.setattr(cli, "notify", lambda t, m: notes.append(m))

    assert cli.cmd_export(cli.build_parser().parse_args(["export", "--notify"])) == 0
    assert "NOT BACKED UP: 1 video" in capsys.readouterr().out
    assert notes == [
        "1 video couldn't be downloaded from iCloud and isn't backed up yet (retrying next run). "
        "See the export log for the file names."
    ]
    cli.cmd_export(cli.build_parser().parse_args(["export", "--notify"]))
    assert len(notes) == 1  # not re-alerted the next run
