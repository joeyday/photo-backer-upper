import os

import pytest

from photo_backer_upper import atomic
from photo_backer_upper.atomic import write_text_atomic
from photo_backer_upper.unavailable import LIVE_UNAVAILABLE_FILE, remember_live_unavailable


def test_write_replaces_file(tmp_path):
    path = tmp_path / "skip-uuids.txt"
    path.write_text("A\nB\n")
    write_text_atomic(path, "C\n")
    assert path.read_text() == "C\n"
    assert os.listdir(tmp_path) == ["skip-uuids.txt"]


def test_failed_write_keeps_old_file_and_leaves_no_temp(tmp_path, monkeypatch):
    path = tmp_path / "skip-uuids.txt"
    path.write_text("A\nB\n")

    def disk_full(*a):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(atomic.os, "replace", disk_full)
    with pytest.raises(OSError):
        write_text_atomic(path, "only half\n")
    assert path.read_text() == "A\nB\n"
    assert os.listdir(tmp_path) == ["skip-uuids.txt"]


def test_live_list_rewrite_keeps_one_header(tmp_path):
    remember_live_unavailable(tmp_path, {"A"})
    remember_live_unavailable(tmp_path, {"B"})
    lines = (tmp_path / LIVE_UNAVAILABLE_FILE).read_text().splitlines()
    assert sum(line.startswith("#") for line in lines) == 2
    assert [line for line in lines if not line.startswith("#")] == ["A", "B"]
