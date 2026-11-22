"""pback: export iCloud Photos to a main archive, then mirror to a secondary drive."""

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .alerts import describe, due_alerts, export_errors, missing_this_run
from .automation import (
    STUCK_HOURS,
    ConfigError,
    LockBusy,
    check_free_space,
    ensure_nas_mounted,
    install_agent,
    notify,
    run_lock,
    uninstall_agent,
)
from .fixdates import fix_dates, undo
from .fixexport import fix_export_names
from .reconcile import SKIP_FILE, reconcile
from .unavailable import (
    LIVE_UNAVAILABLE_FILE,
    load_live_unavailable,
    missing_live_uuids,
    read_uuids,
    remember_live_unavailable,
    write_merged_skip,
)

# Names come from local capture time (see naming.py), not osxphotos' {created}, which is UTC for some items.
NAMING_FILE = Path(__file__).with_name("naming.py")
DIRECTORY_TEMPLATE = f"{{function:{NAMING_FILE}::capture_month}}"
FILENAME_TEMPLATE = f"{{function:{NAMING_FILE}::capture_name}}"
# Everything that writes to the archive or the skip list; a scheduled export mustn't run
# while fix-dates is renaming files or reconcile is rewriting the skip list.
LOCKED_COMMANDS = {"export", "mirror", "run", "reconcile", "fix-dates", "fix-export-names"}
EXPORT_REPORT_FILE = "export-report.db"


def env_dir(name: str) -> Path:
    value = config.get(name)
    if not value:
        raise ConfigError(f"{name} is not set (see .env.example)")
    return Path(value).expanduser()


def require_mounted(path: Path, label: str) -> None:
    """The archive's parent volume must exist, so we never write into an unmounted mount point."""
    if not path.is_dir():
        raise ConfigError(f"{label} not found at {path}; is the volume mounted?")


def main_archive() -> Path:
    """The main backup location, mounting its network share first if a share URL is configured."""
    main_dir = env_dir("PHOTO_BACKER_UPPER_MAIN_DIR")
    ensure_nas_mounted(main_dir)
    require_mounted(main_dir, "Main archive")
    return main_dir


def require_tool(name: str, hint: str) -> None:
    if shutil.which(name) is None:
        raise ConfigError(f"'{name}' not found on PATH. {hint}")


def state_dir() -> Path:
    default = Path.home() / ".local" / "state" / "photo-backer-upper"
    path = Path(config.get("PHOTO_BACKER_UPPER_STATE_DIR", default)).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_cmd(cmd: list) -> int:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.call(cmd)


def run_export(cmd: list, report: Path) -> tuple[int, list]:
    """Run an osxphotos export; return its exit status and the files it failed to export."""
    report.unlink(missing_ok=True)  # never read a previous run's report
    rc = run_cmd(cmd + ["--report", str(report)])
    return rc, export_errors(report)


def cmd_export(args) -> int:
    require_tool("osxphotos", "Install it with: uv tool install osxphotos")
    main_dir = main_archive()

    export_db = state_dir() / "export.db"
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
    skip_file = state_dir() / SKIP_FILE
    skip_uuids = read_uuids(skip_file)
    live_unavailable = load_live_unavailable(state_dir())
    if skip_file.exists() or live_unavailable:
        merged = write_merged_skip(state_dir(), skip_uuids, live_unavailable)
        cmd += ["--skip-uuid-from-file", str(merged)]
    if (
        not skip_file.exists()
        and not args.dry_run
        and any(main_dir.iterdir())
        and not getattr(args, "skip_reconcile_check", False)
    ):
        raise ConfigError(
            "The main archive already has files but no reconcile has been run on this Mac, "
            "so export would duplicate them. Run `pback reconcile` first "
            "(or pass --skip-reconcile-check)."
        )
    if getattr(args, "from_date", None):
        cmd += ["--from-date", args.from_date]
    if getattr(args, "to_date", None):
        cmd += ["--to-date", args.to_date]
    if args.dry_run:
        cmd.append("--dry-run")

    started = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    report = state_dir() / EXPORT_REPORT_FILE
    rc, errors = run_export(cmd, report)
    if rc != 0:
        return rc
    if live_unavailable:
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
        rc, live_errors = run_export(live_cmd, report)
        if rc != 0:
            return rc
        errors += live_errors
    if not args.dry_run:
        new = remember_live_unavailable(state_dir(), missing_live_uuids(export_db, started))
        if new:
            print(
                f"{len(new)} Live Photo video(s) iCloud didn't provide; later runs won't "
                "re-download those photos for them.",
                flush=True,
            )
        report_missing(args, export_db, started)
    if errors:
        # osxphotos exits 0 even when files fail, so fail the run here (which also notifies).
        print(f"EXPORT ERRORS: {len(errors)} file(s) couldn't be exported:", flush=True)
        for path, message in errors:
            print(f"  {path}: {message}", flush=True)
        return 1
    return 0


def report_missing(args, export_db: Path, started: str) -> None:
    """Log every file this run couldn't get from iCloud; notify about new or long-standing ones."""
    missing = missing_this_run(export_db, started)
    due = due_alerts(state_dir(), missing)
    if not missing:
        return
    print(f"NOT BACKED UP: {describe(missing)}:", flush=True)
    for f in missing:
        print(f"  {f}", flush=True)
    if due and getattr(args, "notify", False):
        notify("pback: not backed up", f"{describe(due)}. See the export log for the file names.")


def cmd_reconcile(args) -> int:
    require_tool("osxphotos", "Install it with: uv tool install osxphotos")
    require_tool("exiftool", "Install it with: brew install exiftool")
    main_dir = main_archive()
    return reconcile(main_dir, state_dir(), config.get("PHOTO_BACKER_UPPER_LIBRARY"), args.dry_run)


def cmd_fix_dates(args) -> int:
    if args.undo:
        return undo(Path(args.undo))
    require_tool("exiftool", "Install it with: brew install exiftool")
    main_dir = main_archive()
    # With neither --videos nor --photos, handle both.
    both = not args.videos and not args.photos
    return fix_dates(
        main_dir,
        state_dir(),
        apply=args.apply,
        videos=args.videos or both,
        photos=args.photos or both,
        include_other_names=args.include_other_names,
    )


def cmd_fix_export_names(args) -> int:
    require_tool("osxphotos", "Install it with: uv tool install osxphotos")
    main_dir = main_archive()
    return fix_export_names(
        main_dir, state_dir(), config.get("PHOTO_BACKER_UPPER_LIBRARY"), apply=args.apply
    )


class SecondaryUnavailable(ConfigError):
    """No secondary drive is configured or attached; `run` skips the mirror for this."""


def secondary_archive() -> Path:
    try:
        secondary_dir = env_dir("PHOTO_BACKER_UPPER_SECONDARY_DIR")
        # Require the volume itself (e.g. /Volumes/Secondary), not just the subfolder, so a
        # missing drive is never "created" as a plain folder on the startup disk.
        if len(secondary_dir.parts) >= 3 and secondary_dir.parts[1] == "Volumes":
            require_mounted(Path(*secondary_dir.parts[:3]), "Secondary drive")
    except ConfigError as e:
        raise SecondaryUnavailable(str(e)) from None
    return secondary_dir


def cmd_mirror(args) -> int:
    secondary_dir = secondary_archive()
    require_tool("rsync", "rsync should ship with macOS (Homebrew's newer rsync is recommended).")
    main_dir = main_archive()
    secondary_dir.mkdir(parents=True, exist_ok=True)

    base = ["rsync", "-a"] + (["--delete-during"] if args.delete else [])
    base += [f"{main_dir}/", f"{secondary_dir}/"]
    if not args.dry_run and not getattr(args, "skip_space_check", False):
        # Refuse up front if it can't fit, rather than failing mid-copy with files already deleted.
        check_free_space(base, secondary_dir)

    cmd = ["rsync", "-ah", "--info=progress2"]
    if args.delete:
        cmd.append("--delete-during")
    if args.dry_run:
        cmd.append("--dry-run")
    cmd += [f"{main_dir}/", f"{secondary_dir}/"]
    return run_cmd(cmd)


def cmd_run(args) -> int:
    rc = cmd_export(args)
    if rc != 0:
        print("Export failed; skipping mirror.", file=sys.stderr)
        return rc
    try:
        return cmd_mirror(args)
    except SecondaryUnavailable as e:
        # Only "no drive" is a normal skip. Anything else (e.g. the drive is too small)
        # is a failure, so it reaches main() and is reported and notified.
        print(f"Skipping mirror: {e}", file=sys.stderr)
        return 0


def cmd_install_agent(args) -> int:
    return install_agent(state_dir(), args.every_hours, args.load)


def cmd_uninstall_agent(args) -> int:
    return uninstall_agent()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pback", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, func, help_, delete=False, notify_flag=True):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--dry-run", action="store_true", help="show what would happen")
        if delete:
            sp.add_argument(
                "--delete",
                action="store_true",
                help="also delete files from the secondary that are gone from main",
            )
        if notify_flag:
            sp.add_argument(
                "--notify",
                action="store_true",
                help="show a macOS notification if the run fails (used by the scheduled agent)",
            )
        sp.set_defaults(func=func)
        return sp

    add(
        "reconcile",
        cmd_reconcile,
        "one-time: mark library photos already in the archive so export skips them",
        notify_flag=False,
    )
    fd = sub.add_parser("fix-dates", help="rename archive videos/photos to their real capture date")
    fd.add_argument("--apply", action="store_true", help="actually rename (default is report only)")
    fd.add_argument("--videos", action="store_true", help="only videos")
    fd.add_argument("--photos", action="store_true", help="only photos with camera-style names")
    fd.add_argument(
        "--include-other-names",
        action="store_true",
        help="also rename photos/videos with descriptive names (loses the description)",
    )
    fd.add_argument("--undo", metavar="LOG", help="reverse the renames recorded in a fix-dates log")
    fd.set_defaults(func=cmd_fix_dates)

    fe = sub.add_parser(
        "fix-export-names",
        help="rename files an earlier export named in UTC instead of local capture time",
    )
    fe.add_argument("--apply", action="store_true", help="actually rename (default is report only)")
    fe.set_defaults(func=cmd_fix_export_names)

    ex = add("export", cmd_export, "Photos library -> main archive")
    ex.add_argument(
        "--skip-reconcile-check",
        action="store_true",
        help="export even if reconcile hasn't been run against a non-empty archive",
    )
    ex.add_argument(
        "--from-date", metavar="DATE", help="only items created on or after DATE (e.g. 2025-02-01)"
    )
    ex.add_argument(
        "--to-date", metavar="DATE", help="only items created before DATE (e.g. 2025-03-01)"
    )

    mi = add("mirror", cmd_mirror, "main archive -> secondary drive", delete=True)
    mi.add_argument(
        "--skip-space-check",
        action="store_true",
        help="don't refuse when the copy looks too big for the secondary drive",
    )

    add("run", cmd_run, "export, then mirror if the secondary drive is attached")

    ia = sub.add_parser(
        "install-agent", help="install a launchd agent that runs `pback export` on a schedule"
    )
    ia.add_argument("--every-hours", type=float, default=6, help="run interval (default 6)")
    ia.add_argument(
        "--load", action="store_true", help="also start it now (default: only write the plist)"
    )
    ia.set_defaults(func=cmd_install_agent)
    ua = sub.add_parser("uninstall-agent", help="stop and remove the launchd agent")
    ua.set_defaults(func=cmd_uninstall_agent)
    return p


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "run":
        args.delete = False
    try:
        if args.command in LOCKED_COMMANDS:
            with run_lock(state_dir()):
                rc = args.func(args)
        else:
            rc = args.func(args)
    except LockBusy as e:
        print(e)
        hours = e.held_seconds / 3600
        if hours >= STUCK_HOURS and getattr(args, "notify", False):
            # Repeats every scheduled run, so a stuck run (even a manual one) isn't forgotten.
            notify(
                "pback",
                f"Another pback run has been going for {hours:.0f} hours and may be "
                "stuck; backups are waiting for it.",
            )
        sys.exit(0)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        if getattr(args, "notify", False):
            notify("pback", f"Failed: {e}")
        sys.exit(2)
    except Exception as e:
        # Anything unexpected (a failed osxphotos query, a network share dropping mid-run, a bad
        # database...) would otherwise only leave a traceback in a log nobody reads.
        if getattr(args, "notify", False):
            notify(
                "pback",
                f"{args.command} crashed ({type(e).__name__}); see the log in the state folder",
            )
        raise
    if rc and getattr(args, "notify", False):
        notify("pback", f"{args.command} failed (exit {rc}); see the log in the state folder")
    sys.exit(rc)


if __name__ == "__main__":
    main()
