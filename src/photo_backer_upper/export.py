"""Export the Photos library to the main archive with osxphotos."""

from datetime import datetime, timezone
from pathlib import Path

from . import config
from .alerts import describe, due_alerts, export_errors, missing_this_run
from .errors import ConfigError
from .exportdb import EXPORT_DB_FILE
from .notify import notify
from .paths import main_archive, require_tool, run_cmd, state_dir
from .reconcile import SKIP_FILE
from .unavailable import (
    LIVE_UNAVAILABLE_FILE,
    load_live_unavailable,
    missing_live_uuids,
    read_uuids,
    remember_live_unavailable,
    write_merged_skip,
)

# Names come from local capture time (see naming.py), not osxphotos' {created}, which is UTC for
# some items.
NAMING_FILE = Path(__file__).with_name("naming.py")
DIRECTORY_TEMPLATE = f"{{function:{NAMING_FILE}::capture_month}}"
FILENAME_TEMPLATE = f"{{function:{NAMING_FILE}::capture_name}}"
EXPORT_REPORT_FILE = "export-report.db"


def run_export(cmd: list, report: Path) -> tuple[int, list]:
    """Run an osxphotos export; return its exit status and the files it failed to export."""
    report.unlink(missing_ok=True)  # never read a previous run's report
    rc = run_cmd(cmd + ["--report", str(report)])
    return rc, export_errors(report)


def build_export_cmd(args, main_dir, export_db, skip_file, skip_uuids, live_unavailable) -> list:
    """The osxphotos command for the main export.

    Writes the merged skip file when there is anything to skip, and refuses (ConfigError) to
    export into an archive that already has files when no reconcile has been run on this Mac.
    """
    # fmt: off
    cmd = [
        "osxphotos", "export", str(main_dir),
        "--update",
        # State lives locally so each Mac tracks its own exports into the shared archive.
        "--exportdb", str(export_db),
        "--download-missing", "--use-photokit",
        "--directory", DIRECTORY_TEMPLATE,
        "--filename", FILENAME_TEMPLATE,
        # Skip items merely "Shared with You" (Messages etc.); keep ones saved to the library.
        "--query-eval", "not (photo.syndicated and not photo.saved_to_library)",
        "--verbose",
    ]
    # fmt: on
    library = config.get("PHOTO_BACKER_UPPER_LIBRARY")
    if library:
        cmd += ["--db", library]
    if skip_file.exists() or live_unavailable:
        merged = write_merged_skip(state_dir(), skip_uuids, live_unavailable)
        cmd += ["--skip-uuid-from-file", str(merged)]
    if (
        not skip_file.exists()
        and not args.dry_run
        and any(main_dir.iterdir())
        and not args.skip_reconcile_check
    ):
        raise ConfigError(
            "The main archive already has files but no reconcile has been run on this Mac, "
            "so export would duplicate them. Run `pback reconcile` first "
            "(or pass --skip-reconcile-check)."
        )
    if args.from_date:
        cmd += ["--from-date", args.from_date]
    if args.to_date:
        cmd += ["--to-date", args.to_date]
    if args.dry_run:
        cmd.append("--dry-run")
    return cmd


def export_live_stills(cmd, skip_file, skip_uuids, live_unavailable, report) -> tuple[int, list]:
    """The second pass: the Live Photos whose video iCloud never provides, with `--skip-live`.

    `cmd` is the main export's command. Returns the same as run_export.
    """
    # Their stills still get updated; only the never-delivered video is left out, so
    # osxphotos has nothing to download unless the photo itself changed.
    print(
        f"Exporting {len(live_unavailable)} Live Photos without their unavailable video...",
        flush=True,
    )
    live_cmd = [c for c in cmd if c != "--verbose"]
    i = live_cmd.index("--skip-uuid-from-file")
    live_cmd[i : i + 2] = ["--skip-uuid-from-file", str(skip_file)] if skip_uuids else []
    live_cmd += ["--uuid-from-file", str(state_dir() / LIVE_UNAVAILABLE_FILE), "--skip-live"]
    return run_export(live_cmd, report)


def remember_new_live_unavailable(export_db: Path, started: str) -> None:
    """Add the Live Photo videos this run couldn't get to the list, and say how many were new."""
    new = remember_live_unavailable(state_dir(), missing_live_uuids(export_db, started))
    if new:
        print(
            f"{len(new)} Live Photo video(s) iCloud didn't provide; later runs won't "
            "re-download those photos for them.",
            flush=True,
        )


def report_errors(errors: list) -> int:
    """List the files osxphotos failed to export; returns the run's exit status."""
    if not errors:
        return 0
    # osxphotos exits 0 even when files fail, so fail the run here (which also notifies).
    print(f"EXPORT ERRORS: {len(errors)} file(s) couldn't be exported:", flush=True)
    for path, message in errors:
        print(f"  {path}: {message}", flush=True)
    return 1


def cmd_export(args) -> int:
    require_tool("osxphotos", "Install it with: uv tool install osxphotos")
    main_dir = main_archive()
    export_db = state_dir() / EXPORT_DB_FILE
    skip_file = state_dir() / SKIP_FILE
    skip_uuids = read_uuids(skip_file)
    live_unavailable = load_live_unavailable(state_dir())
    cmd = build_export_cmd(args, main_dir, export_db, skip_file, skip_uuids, live_unavailable)

    started = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    report = state_dir() / EXPORT_REPORT_FILE
    rc, errors = run_export(cmd, report)
    if rc != 0:
        return rc
    if live_unavailable:
        rc, live_errors = export_live_stills(cmd, skip_file, skip_uuids, live_unavailable, report)
        if rc != 0:
            return rc
        errors += live_errors
    if not args.dry_run:
        remember_new_live_unavailable(export_db, started)
        report_missing(args, export_db, started)
    return report_errors(errors)


def report_missing(args, export_db: Path, started: str) -> None:
    """Log every file this run couldn't get from iCloud; notify about new or long-standing ones."""
    missing = missing_this_run(export_db, started)
    due = due_alerts(state_dir(), missing)
    if not missing:
        return
    print(f"NOT BACKED UP: {describe(missing)}:", flush=True)
    for f in missing:
        print(f"  {f}", flush=True)
    if due and args.notify:
        notify("pback: not backed up", f"{describe(due)}. See the export log for the file names.")
