# Backlog

Not part of the MVP.

- **Google redundancy.** Drive for desktop's "Back up to Google Photos" folder sync ended August 10, 2026. The only supported route into Google Photos is the website's "Back up folders" (needs a browser tab open on a computer). Plain Google Drive copy from the NAS (Synology Cloud Sync) still works but won't appear in the Photos app. Revisit later.
- **Replace archive JPGs with HEIC originals** where the Photos library has the original (pre-iCloud photos have none and stay JPG). Build on the reconcile matching; move replaced JPGs to a quarantine folder, never delete. Report first.
- **launchd automation**: `pback install-agent` is written (scheduled `export`, NAS auto-mount, lock, notifications) but not yet tested under launchd; macOS privacy permissions (Photos, Full Disk Access) for the launchd-run executable are the open risk. Still to do: `mirror` when the secondary drive mounts (`StartOnMount`), never with `--delete` unattended.
- **Bigger secondary drive.** The archive (about 2.1 TiB) no longer fits the 1 TB external drive; `mirror` refuses to start. Mirror stays off until a larger drive is in place.
- **Missing originals.** Earlier runs recorded 9 large videos (0.4 to 37 GB) as missing; all of them downloaded on a later run and are in the archive. `export` now logs `NOT BACKED UP:` with the file names whenever iCloud doesn't deliver a photo or video, and with `--notify` (the launchd agent) shows a notification the first time and every 7 days while it stays missing. Edited Live Photo videos are ignored (iCloud may simply not have them).
- **Export re-downloads already-archived items.** Mostly resolved (2026-11-21). The 24-minute run on 2026-11-20 isn't reproducible: runs since take about 4 minutes. osxphotos 0.78.1 checks export.db and the archived file's size/mtime before downloading from iCloud, and every live record matches the NAS, so originals evicted by "Optimize Mac Storage" are not re-fetched. The dry-run's "missing" list is misleading because dry-run skips that check. The one repeat download left was 9 edited Live Photos whose edited video iCloud never provides; `export` now lists those in `live-unavailable-uuids.txt` and exports them in a second `--skip-live` pass (list cleared every 30 days to retry). Not yet confirmed with a real scheduled run; network traffic during a run hasn't been measured.
- **Wife's Mac**: run the same CLI there with its own local state, writing to the same main archive.
- **Approximate-date videos** (UTC `CreateDate` only): `fix-dates` lists but doesn't rename them. Could resolve them by matching the library item and using its local time.

- **`mod_` images (about 4,900).** Mostly derived files (collages, filtered images made for Instagram, screenshots) with no capture date. Their `mod_` date is often the backup date, not the creation date, so it can't be trusted for matching or renaming. Left alone for now. Matching them to library items would need content or perceptual hashing (a drag-out JPG isn't byte-identical to the library original, so a plain hash won't do). Until then, some library screenshots may already exist in the archive under `mod_` names and get exported again as duplicates.

- **Items with a bogus `date_original` (about 170).** Old files (e.g. some Fuji JPGs) whose EXIF time is an import date: their `date` is the right instant but the timezone is unknown, so they're named in UTC and can land on the wrong day or month. Could be fixed by assuming the library owner's timezone at the time, or by hand.
- **Probable duplicates from recovery.** About 330 `(1)` files were added when the reconcile audit was corrected (screenshots and PNGs that couldn't be verified). Review and quarantine, never delete.

## Code review, 2026-11-21

Findings from a full read of the code, checked against osxphotos 0.78.1 and the real state folder. The ones worth the extra code are fixed; the rest are deliberately left alone to keep pback simple.

### Fixed

1. **A Live Photo video that failed to download once was silenced for 30+ days.** Only `_edited.mov` now goes on `live-unavailable-uuids.txt`; a missing original video keeps alerting.
2. **osxphotos file errors never notified.** osxphotos exits 0 with `error: N`. Export now passes `--report export-report.db`, lists any file errors under `EXPORT ERRORS:` and exits 1. Not yet seen in a real run.
3. **A hung run stopped backups silently.** A run that finds the lock held for 4 hours or more notifies "may be stuck" (with `--notify`), so every scheduled run repeats the warning. A losing run no longer wipes the holder's PID and start time from the lock file. The launcher waits at most 10 minutes for the Photos prompt (needs an app rebuild to take effect). Runs are never killed, to avoid partial files.
4. **Crashes didn't notify.** `main` notifies "crashed (ExceptionType)" with `--notify`, then re-raises so the traceback still reaches the log.
5. **`fix-dates` would rename osxphotos' `<date>_edited` copies.** It now skips them. Other exported files aren't recognised; `fix-dates` is for the archive as it was before exporting.
6. **The one-time tools didn't take the run lock.** `reconcile`, `fix-dates` and `fix-export-names` now do, and `fix-dates --apply` re-checks each rename instead of overwriting.
7. **State files were written in place.** `atomic.py` writes a temp file and renames it over the old one, for the skip lists, the Live Photo list, `missing-alerts.json` and `reconcile-unconfirmed.tsv`.
8. **`pback run` reported success when the mirror was refused for space.** Only "no secondary set or attached" is a normal skip now; anything else fails the run.
9. **`requires-python` said 3.9 but the code needs 3.10.** Fixed in `pyproject.toml` and `uv.lock`.

### Won't fix

Not worth the extra code for a family backup tool, or only affect one-time tools that have already been run.

- The Live Photo list's 30-day retry counts from the file's last change, so adding an item pushes it back. The list holds 9 items and rarely grows.
- `install-agent --load` runs `launchctl bootstrap` straight after `bootout`, which might fail while the old job shuts down. Untested; re-running it would fix it.
- An export with `--from-date`/`--to-date` resets alert state for files outside the range, so they alert again on the next full run.
- `fix-dates`: an invalid `DateTimeOriginal` blocks the `CreateDate` fallback; RAW files and older video types (`.avi`, `.mpg`, `.3gp`, `.mts`) are ignored; a bad exiftool chunk counts as "no date"; `--undo` doesn't mount the NAS.
- `reconcile` crashes on bad exiftool output instead of skipping it.
- `install-agent` checks for `pback` on `PATH`, but the launcher only looks in `~/.local/bin`.
- `PHOTO_BACKER_UPPER_LIBRARY` isn't `~`-expanded (documented in the README).
- `require_mounted` checks that the folder exists, not that it's a mount point.
- `fix-export-names` adds an item to the skip list even when its rename was skipped.
- An edited `.mp4` video's missing `_edited.mov` is ignored by the alerts.
- Performance: the log grows about 1 MB per run and is never trimmed (delete it now and then); pback scans the export history table twice per run (well under a second); `fix-dates` and `reconcile` walk the archive more than they need to (one-time tools); `mirror` dry-runs rsync for its space check (that's the check); the config file is re-read on every lookup (negligible). The scheduled run itself takes 2.5 to 4 minutes, almost all of it osxphotos checking files against the NAS, and has little left to gain.

## Code style and structure, 2026-11-22

A read of the code for readability rather than bugs. Nothing here changes behavior. None of it is done yet.

### Findings

The source is about 1,500 lines in 10 modules, the largest 335 lines. Module docstrings and comments explain why, and `naming.py`, `atomic.py` and `alerts.py` each do one thing. The weak spots:

- **Four functions do too much.** `cmd_export` (`cli.py`), `plan_files` and `fix_dates` (`fixdates.py`) and `fix_export_names` (`fixexport.py`) each mix deciding, printing a report and acting.
- **Plans are anonymous tuples.** `fix_dates` reads fields as `p[1]`, `p[4]`, `p[2].parent`.
- **Duplication.** The `%Y-%m-%d_%H-%M-%S` format and its regex are defined four times under four names (`fixdates.py`, `reconcile.py`, `fixexport.py`, `naming.py`). The "missing files" SQL query is copied between `alerts.py` and `unavailable.py`. The `osxphotos query --json` call is written in both `reconcile.py` and `fixexport.py`. The video extensions are listed in `alerts.py` and `fixdates.py`. `getattr(args, "notify", False)` appears six times in `cli.py`, and `cmd_mirror` builds the rsync command twice.
- **Things in the wrong place.** `automation.py` holds five unrelated topics (share mount, run lock, notifications, free-space guard, launchd agent) plus `ConfigError`, the app-wide error. `cli.py` holds the whole export logic. `config.file_values()` only wraps `_file_values()`.
- **Terse names in places:** `fd`, `fe`, `mi`, `ia` in the parser; `cls`, `lw`/`lh`/`aw`/`ah` elsewhere.
- **Thin tests where the logic is densest.** `tests/test_fixdates.py` is 36 lines for 270 lines of code. pytest isn't declared in `pyproject.toml`.

### Decision: Ruff

Use Ruff for both formatting and linting (it covers what Black, isort and flake8 did). Line length 100, otherwise default settings. A dry run on 2026-11-22: the formatter would rewrite 17 of 22 files (cosmetic only); 37 lines are over 100 characters; the linter finds 2 unused imports, unsorted imports in 5 files and one `raise` without `from`.

- Rule sets: `E`, `F`, `W`, `I`, `B`, `UP`, `SIM`. Add `C90` (complexity) only after step 4 below, since the four long functions would fail it today.
- A pre-commit hook kept in the repo (`.githooks/pre-commit`, enabled with `git config core.hooksPath .githooks`) runs `ruff format --check`, `ruff check` and the tests. It refuses the commit rather than reformatting, so a commit never contains changes nobody looked at.
- Add a dev dependency group with `ruff` and `pytest` so `uv run pytest` works.

### Target layout

| Module | Contents |
|---|---|
| `cli.py` | Argument parser and `main()` only |
| `export.py` | The export command, split into: build the osxphotos command, the Live Photo second pass, reporting |
| `mirror.py` | rsync and the free-space guard |
| `errors.py` | `ConfigError`, `LockBusy`, `SecondaryUnavailable` |
| `lock.py`, `mount.py`, `notify.py`, `agent.py` | `automation.py`, split along its own section banners |
| `exportdb.py` | The osxphotos export database queries shared by `alerts.py` and `unavailable.py` |
| `library.py` | The one `osxphotos query` call and the "Shared with You" filter |
| `naming.py` | Also the single home of the timestamp format and regex |
| `fixdates.py`, `fixexport.py` | Kept, each split into plan, report and apply, sharing a `Rename` dataclass and one undo-log writer |

Constraint: osxphotos loads `naming.py` by file path, outside the package, so it can't import from other pback modules. Other modules import from it, not the other way round.

### Order

Each step is its own commit, with the tests passing after each.

1. **Ruff and the hook.** Purely mechanical, and done first so the reformat doesn't muddy later diffs.
2. **Remove the duplication:** timestamp format, SQL query, library query, video extensions.
3. **Split `automation.py` and `cli.py`.** Moving code, not changing it. Tests that patch `cli.cmd_export`, `cli.notify` and similar need their targets updated.
4. **Restructure the four long functions.** The only risky step. Write tests for `plan_files` and `fix_export_names` first, then refactor against them. Then turn on the complexity rule.
