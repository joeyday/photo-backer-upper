"""pback: export iCloud Photos to a main archive, then mirror to a secondary drive."""

import argparse
import sys
from pathlib import Path

from . import config
from .agent import install_agent, uninstall_agent
from .errors import ConfigError, LockBusy, SecondaryUnavailable
from .export import cmd_export
from .fixdates import fix_dates, undo
from .fixexport import fix_export_names
from .lock import STUCK_HOURS, run_lock
from .mirror import cmd_mirror
from .notify import notify
from .paths import main_archive, require_tool, state_dir
from .reconcile import reconcile

# Everything that writes to the archive or the skip list; a scheduled export mustn't run
# while fix-dates is renaming files or reconcile is rewriting the skip list.
LOCKED_COMMANDS = {"export", "mirror", "run", "reconcile", "fix-dates", "fix-export-names"}


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
    parser = argparse.ArgumentParser(prog="pback", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    # Defaults for flags that only some commands define, so the code can read them from any
    # command's arguments (`run` passes its arguments to both export and mirror). A command that
    # defines the flag overrides its default.
    parser.set_defaults(
        notify=False,
        skip_reconcile_check=False,
        from_date=None,
        to_date=None,
        skip_space_check=False,
        delete=False,
    )

    def add(name, func, help_, delete=False, notify_flag=True):
        subparser = sub.add_parser(name, help=help_)
        subparser.add_argument("--dry-run", action="store_true", help="show what would happen")
        if delete:
            subparser.add_argument(
                "--delete",
                action="store_true",
                help="also delete files from the secondary that are gone from main",
            )
        if notify_flag:
            subparser.add_argument(
                "--notify",
                action="store_true",
                help="show a macOS notification if the run fails (used by the scheduled agent)",
            )
        subparser.set_defaults(func=func)
        return subparser

    add(
        "reconcile",
        cmd_reconcile,
        "one-time: mark library photos already in the archive so export skips them",
        notify_flag=False,
    )
    fix_dates_parser = sub.add_parser(
        "fix-dates", help="rename archive videos/photos to their real capture date"
    )
    fix_dates_parser.add_argument(
        "--apply", action="store_true", help="actually rename (default is report only)"
    )
    fix_dates_parser.add_argument("--videos", action="store_true", help="only videos")
    fix_dates_parser.add_argument(
        "--photos", action="store_true", help="only photos with camera-style names"
    )
    fix_dates_parser.add_argument(
        "--include-other-names",
        action="store_true",
        help="also rename photos/videos with descriptive names (loses the description)",
    )
    fix_dates_parser.add_argument(
        "--undo", metavar="LOG", help="reverse the renames recorded in a fix-dates log"
    )
    fix_dates_parser.set_defaults(func=cmd_fix_dates)

    fix_export_names_parser = sub.add_parser(
        "fix-export-names",
        help="rename files an earlier export named in UTC instead of local capture time",
    )
    fix_export_names_parser.add_argument(
        "--apply", action="store_true", help="actually rename (default is report only)"
    )
    fix_export_names_parser.set_defaults(func=cmd_fix_export_names)

    export_parser = add("export", cmd_export, "Photos library -> main archive")
    export_parser.add_argument(
        "--skip-reconcile-check",
        action="store_true",
        help="export even if reconcile hasn't been run against a non-empty archive",
    )
    export_parser.add_argument(
        "--from-date", metavar="DATE", help="only items created on or after DATE (e.g. 2025-02-01)"
    )
    export_parser.add_argument(
        "--to-date", metavar="DATE", help="only items created before DATE (e.g. 2025-03-01)"
    )

    mirror_parser = add("mirror", cmd_mirror, "main archive -> secondary drive", delete=True)
    mirror_parser.add_argument(
        "--skip-space-check",
        action="store_true",
        help="don't refuse when the copy looks too big for the secondary drive",
    )

    add("run", cmd_run, "export, then mirror if the secondary drive is attached")

    install_agent_parser = sub.add_parser(
        "install-agent", help="install a launchd agent that runs `pback export` on a schedule"
    )
    install_agent_parser.add_argument(
        "--every-hours", type=float, default=6, help="run interval (default 6)"
    )
    install_agent_parser.add_argument(
        "--load", action="store_true", help="also start it now (default: only write the plist)"
    )
    install_agent_parser.set_defaults(func=cmd_install_agent)
    uninstall_agent_parser = sub.add_parser(
        "uninstall-agent", help="stop and remove the launchd agent"
    )
    uninstall_agent_parser.set_defaults(func=cmd_uninstall_agent)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.command in LOCKED_COMMANDS:
            with run_lock(state_dir()):
                rc = args.func(args)
        else:
            rc = args.func(args)
    except LockBusy as e:
        print(e)
        hours = e.held_seconds / 3600
        if hours >= STUCK_HOURS and args.notify:
            # Repeats every scheduled run, so a stuck run (even a manual one) isn't forgotten.
            notify(
                "pback",
                f"Another pback run has been going for {hours:.0f} hours and may be "
                "stuck; backups are waiting for it.",
            )
        sys.exit(0)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        if args.notify:
            notify("pback", f"Failed: {e}")
        sys.exit(2)
    except Exception as e:
        # Anything unexpected (a failed osxphotos query, a network share dropping mid-run, a bad
        # database...) would otherwise only leave a traceback in a log nobody reads.
        if args.notify:
            notify(
                "pback",
                f"{args.command} crashed ({type(e).__name__}); see the log in the state folder",
            )
        raise
    if rc and args.notify:
        notify("pback", f"{args.command} failed (exit {rc}); see the log in the state folder")
    sys.exit(rc)


if __name__ == "__main__":
    main()
