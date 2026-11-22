"""cmd_export's first osxphotos command line and its reconcile check.

The Live Photo second pass, file errors and missing-file reporting are covered in
test_unavailable.py. Only the outside world is faked; all paths and dates are made up.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from photo_backer_upper import cli, config, naming
from photo_backer_upper.automation import ConfigError

# --- Fakes and helpers. Every patch is here, so a later step retargets it in one place. ---

# The module that defines cmd_export and the helpers it calls (`export` after #14).
EXPORT_MODULE = cli


@pytest.fixture
def env(tmp_path, monkeypatch):
    """An empty archive and state folder, no settings, and a fake osxphotos that succeeds."""
    e = SimpleNamespace(
        archive=tmp_path / "archive", state=tmp_path / "state", calls=[], settings={}
    )
    e.archive.mkdir()
    e.state.mkdir()
    monkeypatch.setattr(EXPORT_MODULE, "main_archive", lambda: e.archive)
    monkeypatch.setattr(EXPORT_MODULE, "state_dir", lambda: e.state)
    monkeypatch.setattr(EXPORT_MODULE, "require_tool", lambda *a: None)
    monkeypatch.setattr(EXPORT_MODULE, "run_cmd", lambda cmd: e.calls.append(cmd) or 0)
    monkeypatch.setattr(config, "get", lambda name, default=None: e.settings.get(name, default))
    return e


def export(*argv):
    args = cli.build_parser().parse_args(["export", *argv])
    return EXPORT_MODULE.cmd_export(args)


NAMING_FILE = Path(naming.__file__)
LIBRARY = "/Users/example/Pictures/Test Library.photoslibrary"


def first_pass(env, *options):
    """The whole first `osxphotos export` command line, with `options` where they go."""
    return [
        "osxphotos",
        "export",
        str(env.archive),
        "--update",
        "--exportdb",
        str(env.state / "export.db"),
        "--download-missing",
        "--use-photokit",
        "--directory",
        f"{{function:{NAMING_FILE}::capture_month}}",
        "--filename",
        f"{{function:{NAMING_FILE}::capture_name}}",
        "--query-eval",
        "not (photo.syndicated and not photo.saved_to_library)",
        "--verbose",
        *options,
        "--report",
        str(env.state / "export-report.db"),
    ]


# --- First-pass command line ---


@pytest.mark.parametrize(
    "argv, library, options",
    [
        ([], None, []),
        (["--from-date", "2025-02-01"], None, ["--from-date", "2025-02-01"]),
        (["--to-date", "2025-03-01"], None, ["--to-date", "2025-03-01"]),
        (["--dry-run"], None, ["--dry-run"]),
        ([], LIBRARY, ["--db", LIBRARY]),
        (
            ["--dry-run", "--to-date", "2025-03-01", "--from-date", "2025-02-01"],
            LIBRARY,
            ["--db", LIBRARY, "--from-date", "2025-02-01", "--to-date", "2025-03-01", "--dry-run"],
        ),
    ],
    ids=["plain", "from-date", "to-date", "dry-run", "library", "everything"],
)
def test_first_pass_command_line(env, argv, library, options):
    if library:
        env.settings["PHOTO_BACKER_UPPER_LIBRARY"] = library
    assert export(*argv) == 0
    assert env.calls == [first_pass(env, *options)]


def test_first_pass_command_line_with_a_skip_list(env):
    (env.state / "skip-uuids.txt").write_text("SKIP-1\n")
    env.settings["PHOTO_BACKER_UPPER_LIBRARY"] = LIBRARY
    assert export("--from-date", "2025-02-01", "--to-date", "2025-03-01", "--dry-run") == 0
    merged = env.state / "skip-uuids.merged.txt"
    assert env.calls == [
        first_pass(
            env,
            "--db",
            LIBRARY,
            "--skip-uuid-from-file",
            str(merged),
            "--from-date",
            "2025-02-01",
            "--to-date",
            "2025-03-01",
            "--dry-run",
        )
    ]
    assert merged.read_text() == "SKIP-1\n"


# --- Refusing to export into an archive that was never reconciled ---

REFUSAL = (
    "The main archive already has files but no reconcile has been run on this Mac, "
    "so export would duplicate them. Run `pback reconcile` first "
    "(or pass --skip-reconcile-check)."
)


def fill(archive, what):
    if what == "photo":
        (archive / "2025-06").mkdir()
        (archive / "2025-06" / "2025-06-01_10-00-00.heic").write_bytes(b"photo")
    elif what == "empty folder":
        (archive / "2025-06").mkdir()
    elif what == "Finder file":
        (archive / ".DS_Store").write_bytes(b"finder")


@pytest.mark.parametrize("what", ["photo", "empty folder", "Finder file"])
def test_refuses_a_non_empty_archive_until_reconcile_has_run(env, what):
    fill(env.archive, what)
    with pytest.raises(ConfigError) as refused:
        export()
    assert str(refused.value) == REFUSAL
    assert env.calls == []


def test_skip_reconcile_check_gets_past_the_refusal(env):
    fill(env.archive, "photo")
    assert export("--skip-reconcile-check") == 0
    assert env.calls == [first_pass(env)]


def test_dry_run_gets_past_the_refusal(env):
    fill(env.archive, "photo")
    assert export("--dry-run") == 0
    assert env.calls == [first_pass(env, "--dry-run")]


def test_an_existing_skip_list_means_reconcile_has_run(env):
    fill(env.archive, "photo")
    (env.state / "skip-uuids.txt").write_text("")
    assert export() == 0
    merged = env.state / "skip-uuids.merged.txt"
    assert env.calls == [first_pass(env, "--skip-uuid-from-file", str(merged))]
    assert merged.read_text() == ""
