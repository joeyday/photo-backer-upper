from photo_backer_upper import fixdates
from photo_backer_upper.fixdates import scan


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


def test_apply_never_overwrites_a_name_taken_after_planning(tmp_path, monkeypatch, capsys):
    main = tmp_path / "archive"
    (main / "2025-06").mkdir(parents=True)
    state = tmp_path / "state"
    state.mkdir()
    (main / "2025-06" / "IMG_0001.jpg").write_bytes(b"old")

    def fake_exiftool(paths, tags, group):
        return {p: {"DateTimeOriginal": "2025-06-02_08-00-00"} for p in paths}

    real_plan = fixdates.plan_files

    def plan_then_clash(*args):
        real_plan(*args)
        if args[3] == "photo":
            # Something else creates the target name between planning and the rename.
            (main / "2025-06" / "2025-06-02_08-00-00.jpg").write_bytes(b"newer, different size")

    monkeypatch.setattr(fixdates, "exiftool_json", fake_exiftool)
    monkeypatch.setattr(fixdates, "plan_files", plan_then_clash)
    fixdates.fix_dates(main, state, apply=True)
    assert (main / "2025-06" / "IMG_0001.jpg").read_bytes() == b"old"
    assert (main / "2025-06" / "2025-06-02_08-00-00.jpg").read_bytes() == b"newer, different size"
    assert "Skipped 1 that changed since the scan" in capsys.readouterr().out
