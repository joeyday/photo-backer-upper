# Photo Backer Upper

Automates backing up a family's iCloud Photos to a NAS and an external drive, with every file named by capture date/time and filed into `YYYY-MM` folders.

> Status: MVP in progress. See [BACKLOG.md](BACKLOG.md) for what's deferred.

## The original (manual) process

1. Two people take photos on iPhones, which sync to iCloud Photos.
2. Macs use "Optimize Mac Storage", so full-quality originals aren't guaranteed to be local. Before each backup, photos were dragged into a staging folder to force originals to download.
3. An rsync script copied the staging folders to a Synology NAS.
4. Hazel rules renamed each file using the date it was taken and moved it into a month folder. They ran `exiftool` with a fallback chain: `DateTimeOriginal`, then `CreateDate`, then `FileModifyDate` (prefixed `mod_`). Result: `YYYY-MM/YYYY-MM-DD_HH-MM-SS[-n].ext`.
   - Known flaw: videos usually fell through to the date they were copied, not the date they were shot.
5. After renaming and organizing, the NAS was mirrored to an external drive (always the last step).
6. Google Drive's Mac app synced the folder for one more redundancy (no longer works; see backlog).

## Target design

- **Stateless**: each run works out what hasn't been backed up yet.
- **No staging step**: export originals straight from the Photos library with [osxphotos](https://github.com/RhetTbull/osxphotos), downloading from iCloud as needed, using the library's true capture date (correct for videos too).
- **Same end state**: files named `YYYY-MM-DD_HH-MM-SS.ext` in `YYYY-MM/` folders on the main archive (NAS) and the secondary archive (external drive). Everyone's photos mix in one tree.
- **What's included**: everything in your library, including screenshots, but not items that were only "Shared with You" (Messages etc.) and never saved to the library.
- **CLI with separate steps**: `export` (Photos to main), `mirror` (main to secondary, always last), `run` (both).
- The mirror never deletes from the secondary unless you pass `--delete`.

## Setup

```sh
brew install exiftool
uv tool install osxphotos   # osxphotos isn't in Homebrew; pipx install osxphotos also works
uv tool install .           # installs the `pback` command from this repo
```

For `mirror`, also `brew install rsync`: macOS's built-in rsync is too old for the progress display.

Settings are environment variables; nothing personal is hardcoded (see `.env.example`). Put them in `~/.zshrc`, or, for scheduled runs, in the config file `~/.config/photo-backer-upper/config` (same `KEY=VALUE` names), because launchd doesn't read `~/.zshrc`. Real environment variables win over the file.

```sh
export PHOTO_BACKER_UPPER_MAIN_DIR="/Volumes/Main/Photos"
export PHOTO_BACKER_UPPER_SECONDARY_DIR="/Volumes/Secondary/Photos"
# optional: export PHOTO_BACKER_UPPER_NAS_URL="smb://user@nas/Share"   # auto-mount the NAS when it isn't mounted
# optional: export PHOTO_BACKER_UPPER_LIBRARY="/path/to/Photos Library.photoslibrary"
# optional: export PHOTO_BACKER_UPPER_STATE_DIR="$HOME/.local/state/photo-backer-upper"
```

`PHOTO_BACKER_UPPER_NAS_URL` is mounted the way Finder's Connect to Server does, so connect once in Finder with "Remember this password in my keychain" ticked.

## Usage

**First, once per Mac:** if the main archive already has photos, run the reconcile so they aren't exported again as duplicates. It only reads the archive (nothing is modified, replaced or deleted) and saves a list of library photos already backed up. A photo counts as backed up only if an archive file has its capture-time name *and* agrees on camera model, sub-second capture time, aspect ratio (and, for videos, duration), because the archive holds several people's photos and two can share a capture second. Anything unconfirmed is exported instead (at worst a duplicate with a ` (1)` suffix, never a missing photo) and listed in `reconcile-unconfirmed.tsv` in the state directory. It needs `exiftool`. Run it on each Mac that exports (e.g. your partner's too); `export` refuses to run against a non-empty archive until it has been.

```sh
pback reconcile --dry-run   # report only
pback reconcile             # save the skip list locally
```

**Before the reconcile, normalize the archive's names.** Reconcile matches by capture-time filename, so files named otherwise are invisible to it. `fix-dates` renames files to `YYYY-MM/YYYY-MM-DD_HH-MM-SS.ext` using the date embedded in each file:

- **Videos:** every video whose name differs from its local capture time (QuickTime `CreationDate`). That includes `mod_` names, UTC-based names (hours off, sometimes the wrong day), and names from the copy date.
- **Photos:** only those with camera-style names (`IMG_1234`, `DSC_0001`, `PXL_...`, `100_0789`), renamed from `DateTimeOriginal`. Photos already named with a capture time are left alone.
- **Folders:** a file in a folder named exactly `YYYY-MM` is renamed and also moved to the folder for its corrected month. A file in any other folder, such as `2005-01 Family Trip`, is renamed in place and never moved, so you can reorganize those yourself later. Files loose at the archive root are not touched at all.
- **Left alone:** `mod_` images (derived files whose date is often the backup date), descriptive names like `2007 Family Reunion 023.jpg` (unless `--include-other-names`, which loses the description), files with no usable embedded date, videos with only a UTC time (approximate), and any file whose rename would sit next to an existing same-sized file (a probable duplicate; reported, not renamed).

It reports by default (summary in the terminal, full plan in a report file under the state directory), writes an undo log when it renames, and your secondary drive needs one `pback mirror --delete` afterwards to drop the old names.

```sh
pback fix-dates                    # report only
pback fix-dates --apply            # rename, writing an undo log
pback fix-dates --videos --apply   # or --photos: one group at a time
pback fix-dates --undo LOG
```

Candidate matches are by capture-time filename (`YYYY-MM-DD_HH-MM-SS`, with `-N` or ` (N)` collision suffixes, any extension), then confirmed by metadata as above. Run `fix-dates` first so older videos and camera-named photos are named by capture time; otherwise they won't match and would be exported again.

```sh
pback export --dry-run   # preview what would be exported
pback export             # Photos library -> main archive
pback export --from-date 2025-02-01 --to-date 2025-03-01   # just a date range
pback mirror             # main archive -> secondary drive
pback run                # export, then mirror if the drive is attached
```

The export state database is kept on the local Mac (not on the NAS), so each Mac tracks its own exports while writing into the shared archive.

**Names use local capture time.** osxphotos' built-in `{created}` is UTC with no timezone for many older or imported items, which puts names hours off (sometimes the wrong day or month). `naming.py` uses the library's local `date_original` when it is the same instant as `date` but in a different timezone, and otherwise keeps `date`. Items whose `date_original` is bogus (e.g. an import date) can't be corrected and keep an approximate, UTC-based name. If an earlier export named files in UTC, `pback fix-export-names` (report first, `--apply` to rename, with an undo log in the same format as `fix-dates`) renames them and adds those items to the skip list.

Items whose originals can't be downloaded are reported as "missing" and skipped; re-running `export` retries them (opening the item in Photos can force the download).

**Size the secondary drive for the whole archive.** `mirror` first dry-runs rsync and refuses to start if the copy won't fit in the drive's free space, since a full drive part-way through a `--delete` mirror can leave the copy half-updated. rsync doesn't detect renames, so after `fix-dates` a mirror re-copies the renamed files in full.

## Automation

```sh
pback install-agent --every-hours 6 --load   # launchd agent: `pback export --notify` at login and every 6 hours
pback uninstall-agent
```

The agent writes its log to `logs/export.log` in the state directory and shows a notification if a run fails. Only one `export`/`mirror` runs at a time, so a scheduled run that overlaps a long one exits quietly. If the NAS isn't mounted and `PHOTO_BACKER_UPPER_NAS_URL` is set, it mounts it first; otherwise the run is skipped with an error. Settings come from the config file, not `~/.zshrc`. The Mac must be awake and logged in, and the executable running the job needs Photos access ("All Photos") and Full Disk Access in System Settings, privacy and security. The mirror is deliberately not scheduled.
