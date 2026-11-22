from photo_backer_upper.alerts import REALERT_DAYS, describe, due_alerts, missing_this_run
from test_unavailable import make_db

T = "2026-11-21 18:00:01.000"


def test_missing_this_run_ignores_edited_live_video_and_aae(tmp_path):
    db = tmp_path / "export.db"
    make_db(
        db,
        history=[
            ("2025-01/a.mov", "VIDEO", T, "missing"),  # plain video: alert
            ("2025-01/b.heic", "PHOTO", T, "missing"),  # photo: alert
            ("2025-01/c.mov", "LIVE", T, "missing"),  # original live video: alert
            ("2025-01/c_edited.mov", "LIVE", T, "missing"),  # edited live video: ignore
            ("2025-01/d.aae", "AAE", T, "missing"),  # sidecar: ignore
            ("2025-01/e.mov", "OLD", "2026-11-20 00:00:00.000", "missing"),  # earlier run
        ],
        records=[("2025-01/c.heic", "LIVE")],
    )
    assert missing_this_run(db, "2026-11-21 18:00:00") == [
        "2025-01/a.mov",
        "2025-01/b.heic",
        "2025-01/c.mov",
    ]


def test_due_alerts_new_then_quiet_then_realert(tmp_path):
    day = 86400
    assert due_alerts(tmp_path, ["a", "b"], now=0) == ["a", "b"]
    assert due_alerts(tmp_path, ["a", "b", "c"], now=day) == ["c"]
    assert due_alerts(tmp_path, ["a"], now=REALERT_DAYS * day) == ["a"]
    # b recovered and was forgotten, so if it fails again it alerts right away
    assert due_alerts(tmp_path, ["b"], now=REALERT_DAYS * day + 1) == ["b"]


def test_describe():
    assert (
        describe(["x.heic"])
        == "1 photo couldn't be downloaded from iCloud and isn't backed up yet (retrying next run)"
    )
    assert describe(["x.heic", "y.jpg", "z.mov"]) == (
        "2 photos and 1 video couldn't be downloaded from iCloud and aren't backed up yet (retrying next run)"
    )
