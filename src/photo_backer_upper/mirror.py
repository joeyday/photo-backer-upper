"""Mirror the main archive to the secondary drive."""

import re
import shutil
import subprocess
from pathlib import Path

from .errors import ConfigError
from .paths import main_archive, require_tool, run_cmd, secondary_archive

TRANSFER_RE = re.compile(r"Total transferred file size:\s*([\d,]+)")


def bytes_to_transfer(rsync_cmd: list) -> int:
    """Dry-run the rsync command with --stats and return how many bytes it would copy."""
    out = subprocess.run(
        rsync_cmd + ["--dry-run", "--stats"], capture_output=True, text=True
    ).stdout
    m = TRANSFER_RE.search(out)
    if not m:
        raise ConfigError(
            "couldn't work out how much the mirror would copy "
            "(use --skip-space-check to mirror anyway)"
        )
    return int(m.group(1).replace(",", ""))


def check_free_space(rsync_cmd: list, dest: Path) -> None:
    need = bytes_to_transfer(rsync_cmd)
    free = shutil.disk_usage(dest).free
    gib = 1 << 30
    print(f"Mirror would copy {need / gib:.0f} GiB; {free / gib:.0f} GiB free.", flush=True)
    if need > free:
        raise ConfigError(
            f"the mirror would copy {need / gib:.0f} GiB but the secondary drive has only "
            f"{free / gib:.0f} GiB free. Not starting (a full drive mid-mirror, especially "
            f"with --delete, can leave the copy half-updated). Use a bigger drive."
        )


def cmd_mirror(args) -> int:
    secondary_dir = secondary_archive()
    require_tool("rsync", "rsync should ship with macOS (Homebrew's newer rsync is recommended).")
    main_dir = main_archive()
    secondary_dir.mkdir(parents=True, exist_ok=True)

    delete = ["--delete-during"] if args.delete else []
    paths = [f"{main_dir}/", f"{secondary_dir}/"]
    if not args.dry_run and not args.skip_space_check:
        # Refuse up front if it can't fit, rather than failing mid-copy with files already deleted.
        check_free_space(["rsync", "-a", *delete, *paths], secondary_dir)

    dry_run = ["--dry-run"] if args.dry_run else []
    return run_cmd(["rsync", "-ah", "--info=progress2", *delete, *dry_run, *paths])
