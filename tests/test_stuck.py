import os
import sys
import time

import pytest

from photo_backer_upper import cli
from photo_backer_upper.automation import LockBusy, run_lock


def test_busy_lock_reports_holder_age_and_keeps_its_pid(tmp_path):
    with run_lock(tmp_path):
        path = tmp_path / "pback.lock"
        assert path.read_text() == str(os.getpid())
        old = time.time() - 5 * 3600
        os.utime(path, (old, old))
        with pytest.raises(LockBusy) as busy:
            with run_lock(tmp_path):
                pass
        assert busy.value.held_seconds >= 5 * 3600 - 5
        assert path.read_text() == str(os.getpid())   # the loser didn't truncate it


def test_main_notifies_when_lock_held_too_long(tmp_path, monkeypatch):
    notes = []
    monkeypatch.setattr(cli, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "notify", lambda t, m: notes.append(m))
    monkeypatch.setattr(sys, "argv", ["pback", "export", "--notify"])
    with run_lock(tmp_path):
        old = time.time() - 5 * 3600
        os.utime(tmp_path / "pback.lock", (old, old))
        with pytest.raises(SystemExit) as exit_:
            cli.main()
    assert exit_.value.code == 0
    assert len(notes) == 1 and "5 hours" in notes[0]


def test_main_notifies_on_crash_and_keeps_traceback(tmp_path, monkeypatch):
    notes = []

    def boom(args):
        raise RuntimeError("network share went away")

    monkeypatch.setattr(cli, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "notify", lambda t, m: notes.append(m))
    monkeypatch.setattr(cli, "cmd_export", boom)
    monkeypatch.setattr(sys, "argv", ["pback", "export", "--notify"])
    with pytest.raises(RuntimeError):
        cli.main()
    assert notes == ["export crashed (RuntimeError); see the log in the state folder"]
    # The lock was released on the way out.
    with run_lock(tmp_path):
        pass


@pytest.mark.parametrize("argv", [["reconcile"], ["fix-dates", "--apply"], ["fix-export-names"]])
def test_one_time_tools_wait_for_the_lock(tmp_path, monkeypatch, argv):
    ran = []
    monkeypatch.setattr(cli, "state_dir", lambda: tmp_path)
    for name in ("cmd_reconcile", "cmd_fix_dates", "cmd_fix_export_names"):
        monkeypatch.setattr(cli, name, lambda args: ran.append(args) or 0)
    monkeypatch.setattr(sys, "argv", ["pback", *argv])
    with run_lock(tmp_path):
        with pytest.raises(SystemExit):
            cli.main()
    assert ran == []


def test_main_quiet_when_lock_recently_taken(tmp_path, monkeypatch):
    notes = []
    monkeypatch.setattr(cli, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "notify", lambda t, m: notes.append(m))
    monkeypatch.setattr(sys, "argv", ["pback", "export", "--notify"])
    with run_lock(tmp_path):
        with pytest.raises(SystemExit):
            cli.main()
    assert notes == []
