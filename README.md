# Photo Backer Upper

`pback` backs up an iCloud Photos library to a folder you choose (the **main backup location**) and, when you ask, copies that folder to a second one (the **secondary backup location**). Every file is named by its capture time and filed into a month folder: `YYYY-MM/YYYY-MM-DD_HH-MM-SS.ext`.

Either location can be any folder the Mac can write to: a folder on the Mac's own disk, an external drive, or a network share. If the main backup location is on a network share, pback can mount the share itself when it isn't connected. Several Macs can back up into the same main backup location, each keeping its own record of what it has exported.

pback is a thin layer over three existing tools: [osxphotos](https://github.com/RhetTbull/osxphotos) does the export, `exiftool` reads dates out of files already in the backup, and `rsync` does the copy to the secondary backup location.

> Status: MVP in progress. See the [issues](https://github.com/joeyday/photo-backer-upper/issues) for what's deferred.

## Install (automatic backups on a Mac)

Do these in order, on each Mac that backs up photos. You need macOS, [Homebrew](https://brew.sh), the Xcode command line tools, and the Photos library signed in to iCloud on that Mac. Scheduled runs only happen while the Mac is awake and you are logged in.

**0. Get the project files.**

```sh
xcode-select --install       # skip if already installed; includes git and the Swift compiler
git clone https://github.com/joeyday/photo-backer-upper.git ~/Projects/photo-backer-upper
cd ~/Projects/photo-backer-upper
```

Every command below is run from that folder. To update later: `git pull`, then `uv tool install . --reinstall`.

**1. Install the tools.**

```sh
brew install exiftool uv rsync
uv tool install osxphotos
uv tool install .            # installs the `pback` command into ~/.local/bin
uv tool update-shell         # puts ~/.local/bin on your PATH; open a new Terminal afterwards
```

`pback` must end up at `~/.local/bin/pback`, because the launcher app (step 5) looks for it there and nowhere else. `rsync` is only needed for the secondary backup location.

**2. Choose the main backup location.** Create the folder if it doesn't exist yet; pback won't create it. If it's on a network share, connect to the share once in Finder (Go, Connect to Server) and tick "Remember this password in my keychain". You can disconnect afterwards; pback reconnects when it needs to.

**3. Create the config file.** Scheduled runs can't see `~/.zshrc`, so the settings must live here:

```sh
mkdir -p ~/.config/photo-backer-upper
cat > ~/.config/photo-backer-upper/config <<'EOF'
PHOTO_BACKER_UPPER_MAIN_DIR=/Volumes/Backup/Photos
EOF
chmod 600 ~/.config/photo-backer-upper/config
```

Use your own folder. If it's on a network share, also add the share's address, so pback can mount it when it isn't connected:

```sh
echo 'PHOTO_BACKER_UPPER_NAS_URL=smb://user@server/Backup' >> ~/.config/photo-backer-upper/config
```

All settings are listed under [Settings](#settings).

**4. If the main backup location already has photos, run reconcile** (once per Mac) so they aren't exported again as duplicates. It only reads the backup. See [Tools for existing backups](#tools-for-existing-backups) for what it does, and for `fix-dates`, which should be run before it.

```sh
pback reconcile --dry-run
pback reconcile
```

`pback export` refuses to run into a folder that already has files until this has been done on that Mac.

**5. Build the launcher app.** macOS privacy permissions attach to the program that runs. `pback` is a Python script, so a small signed app starts it and holds the permissions instead of your whole Python:

```sh
security find-identity -v -p codesigning        # find your signing identity
SIGN_IDENTITY="<identity from above>" sh launcher/build.sh
```

This builds `~/Applications/Photo Backer Upper.app`. If the first command lists no identity, run `sh launcher/build.sh` on its own. That signs ad hoc, which works, but the signature changes on every build, so macOS may ask for the permissions again after each rebuild.

**6. Grant permissions, once.** In Finder, open `~/Applications` and double-click **Photo Backer Upper**. Click **Allow Access to All Photos**. If the main backup location is on an external drive or a network share, allow access to it when macOS asks. The app has no window. It then runs `pback export --dry-run`, which changes nothing and takes a few minutes; you can watch it in Activity Monitor.

**7. Start the schedule.**

```sh
pback install-agent --every-hours 6 --load
```

This runs an export straight away, then at every login and every 6 hours. To check on it:

```sh
tail ~/.local/state/photo-backer-upper/logs/export.log
```

A good run starts with `Photos access status: 3` and ends with a `Processed:` line showing `error: 0`. To stop the schedule: `pback uninstall-agent`.

The schedule only exports to the main backup location. Copying to the secondary backup location is done by hand with `pback mirror` (see [Copying to the secondary backup location](#copying-to-the-secondary-backup-location)).

## How it works

### The pieces

| Piece | File | What it does |
| --- | --- | --- |
| Command line | `src/photo_backer_upper/cli.py` | Parses commands, builds the `osxphotos` and `rsync` command lines and runs them. |
| Settings | `config.py` | Reads settings from the environment, then the config file. |
| Naming | `naming.py` | Functions that osxphotos calls for each photo to get its folder and file name. |
| Unattended runs | `automation.py` | Mounting a network share, the run lock, notifications, the free-space check, the launchd agent. |
| Missing-file alerts | `alerts.py` | Finds files a run couldn't download from iCloud and decides when to notify. |
| Unavailable Live Photo videos | `unavailable.py` | Keeps a list of Live Photos whose video iCloud doesn't deliver. |
| Safe file writes | `atomic.py` | Replaces state files whole, so an interrupted write never leaves half a file. |
| Reconcile | `reconcile.py` | One-time: works out which library photos are already in the backup. |
| Fix dates | `fixdates.py` | One-time: renames files in the backup to their embedded capture date. |
| Fix export names | `fixexport.py` | One-time: renames files an earlier version of export named in UTC. |
| Launcher | `launcher/PhotoBackerUpper.swift` | The app launchd runs. Checks Photos access, then starts `pback`. |

The Python package has no dependencies beyond the standard library. It needs Python 3.10 or newer. `osxphotos`, `exiftool` and `rsync` are run as separate programs and must be on `PATH`.

### What happens in a scheduled run

1. **launchd** starts the launcher app with the arguments `export --notify`.
2. **The launcher** asks for Photos access if that hasn't been decided yet (waiting at most 10 minutes for an answer), logs the access status, sets `PATH` to `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin` and the system folders, runs `~/.local/bin/pback` with the same arguments, and exits with pback's exit status. Started with no arguments (double-clicked in Finder), it runs `pback export --dry-run`.
3. **pback takes the run lock** (`pback.lock` in the state folder, holding the PID of the run that has it). Every command except `install-agent` and `uninstall-agent` takes it. If another one is already running, the new one prints a message and exits with status 0. If the other one has held the lock for 4 hours or more and `--notify` is set, it also sends a "may be stuck" notification, so every scheduled run repeats the warning until the stuck run ends.
4. **pback finds the main backup location.** If the folder exists, it carries on. If it doesn't and a share URL is set (`PHOTO_BACKER_UPPER_NAS_URL`), pback waits up to 120 seconds for the server to accept a connection, asks macOS to mount the share (the same call as Finder's Connect to Server, with the password from your keychain), and waits up to 20 seconds for the folder to appear. If the server never answers, it doesn't try to mount. If the folder still isn't there, the run fails with "Main archive not found".
5. **pback runs `osxphotos export`** into the main backup location (details below).
6. **pback checks what osxphotos couldn't get** and logs or notifies about it (see [Missing files](#missing-files)).

There is no time limit on a run. A run that hangs is only noticed by the next scheduled run that finds the lock (step 3), so the first "may be stuck" notification comes 4 to 10 hours after it started, with the default 6-hour schedule. pback never stops a run itself, because killing osxphotos or rsync part-way through a copy could leave partial files in the backup.

### Export

`pback export` runs:

```
osxphotos export <main backup location> --update --exportdb <state>/export.db
    --download-missing --use-photokit
    --directory <month folder> --filename <capture time>
    --query-eval "not (photo.syndicated and not photo.saved_to_library)"
    --verbose --report <state>/export-report.db
```

- `--update` with a local export database means each run only copies what isn't in the backup yet. The database lives in the state folder on the Mac, not in the backup, so each Mac tracks its own exports.
- `--download-missing --use-photokit` fetches originals from iCloud when "Optimize Mac Storage" has removed them from the Mac.
- The query leaves out items that were only "Shared with You" (Messages and so on) and never saved to the library. Everything else in the library is exported, screenshots included.
- osxphotos' own defaults apply to the rest: an edited photo is exported as both the original and a second file with an `_edited` suffix, a Live Photo's video is exported as a `.mov` beside the still, and a name that is already taken gets a ` (1)` suffix.
- If `skip-uuids.txt` exists (written by reconcile), those items are skipped.
- `PHOTO_BACKER_UPPER_LIBRARY`, `--from-date`, `--to-date` and `--dry-run` are passed through to osxphotos.

**The reconcile guard.** If the main backup location has anything in it, `skip-uuids.txt` doesn't exist, and this isn't a dry run, export refuses to start, because it would copy the whole library in again. `--skip-reconcile-check` overrides it.

**Exit status.** If osxphotos exits with a non-zero status, pback stops and exits with the same status. osxphotos can also fail on individual files and still exit with 0, counting them as `error: N` in its summary. pback reads those from `export-report.db`, lists each file and its error under `EXPORT ERRORS:` at the end of the log, and exits with status 1. With `--notify` that sends a failure notification, and `pback run` skips the copy to the secondary backup location. Missing files are not errors (see below).

### File names

osxphotos' built-in `{created}` is the library's `date` field, which Photos stores in UTC for many older or imported items. Names made from it can be hours off, sometimes on the wrong day or in the wrong month. `naming.py` picks the time like this:

- If the library also has a `date_original`, it is within 2 seconds of `date`, and the two have different UTC offsets, use `date_original`. That is the same moment written in the local time where the photo was taken.
- Otherwise use `date`. This covers items with no `date_original`, items where both already have the same offset, and items whose `date_original` is a different moment altogether (for example an import date).

### Missing files

osxphotos records a file it couldn't download from iCloud as "missing" and still exits successfully. After every real export, pback reads those records from the export database for the run that just finished.

- Every missing file is printed in the log under `NOT BACKED UP:`.
- With `--notify`, a notification is sent the first time a file is missing and again every 7 days while it stays missing. The record of what has been alerted is `missing-alerts.json`.
- Two kinds are left out: `.AAE` adjustment files, and `*_edited.mov` files belonging to an item that has a non-`.mov` file recorded in the export database (the edited video of a Live Photo).

**Live Photos whose edited video never arrives.** When an `_edited.mov` is missing for an item that already has a non-`.mov` file recorded in the export database, pback adds the item to `live-unavailable-uuids.txt`. A missing original Live Photo video is not added; it stays in the `NOT BACKED UP:` list and is retried every run. On later runs those items are left out of the main export and exported in a second `osxphotos` pass with `--skip-live`, so the still is kept up to date and the video isn't requested again. The list is deleted, and every item on it retried, once the file has gone 30 days without changing. Adding an item counts as a change. Delete the file to retry sooner.

### Copying to the secondary backup location

`pback mirror` copies the main backup location to the secondary one (`PHOTO_BACKER_UPPER_SECONDARY_DIR`):

```
rsync -ah --info=progress2 [--delete-during] <main backup location>/ <secondary backup location>/
```

- Nothing is deleted from the secondary backup location unless you pass `--delete`.
- If the secondary backup location is under `/Volumes` (an external drive or a mounted share), that volume must already be mounted, so an unplugged drive is never replaced by a plain folder on the Mac's own disk. pback doesn't mount anything for the secondary backup location. The folder itself is created if needed.
- Before copying, pback dry-runs rsync with `--stats` and refuses to start if the amount to copy is more than the free space there. `--skip-space-check` turns that off. The check doesn't count space that `--delete` would free.
- `--info=progress2` needs rsync 3.1 or newer. The rsync that ships with macOS doesn't accept it, so install Homebrew's.
- rsync doesn't detect renames. After `fix-dates` or `fix-export-names`, a mirror copies the renamed files again in full, and the old names stay in the secondary backup location until a `pback mirror --delete`.

`pback run` does an export and then a mirror, never with `--delete`. If the export fails, the mirror is skipped. If no secondary backup location is set, or its volume isn't mounted, `run` prints `Skipping mirror: ...` and exits with status 0. That is a normal skip, so rsync doesn't need to be installed for it. Any other reason the mirror can't start, such as the space check refusing or rsync missing, fails the run with status 2 and, with `--notify`, a notification.

### The scheduled agent

`pback install-agent` writes `~/Library/LaunchAgents/com.photo-backer-upper.export.plist`. The agent runs the launcher app with `export --notify` when it is loaded (at login) and every `--every-hours` hours after that. It only exports; the mirror is never scheduled. Output is appended to `logs/export.log` in the state folder. Nothing trims that file.

`install-agent` refuses to write the plist unless `pback` is on `PATH`, the launcher app exists in `~/Applications`, and `PHOTO_BACKER_UPPER_MAIN_DIR` is set in the config file (not just in the environment). Without `--load` it only writes the plist and prints the `launchctl` command that starts it.

With `--notify`, pback shows a macOS notification when it stops with a configuration error (for example the main backup location can't be found), when the command exits with a non-zero status, when it finds another run that may be stuck (see above), when it crashes with an unexpected Python error (the traceback is still written to the log, and the exit status is 1), and for missing files as described above.

## Commands

| Command | What it does |
| --- | --- |
| `pback export` | Photos library to the main backup location. |
| `pback mirror` | Main backup location to the secondary backup location. |
| `pback run` | `export`, then `mirror` if it can. |
| `pback reconcile` | One-time per Mac: record which library photos are already in the backup. |
| `pback fix-dates` | One-time per backup: rename files to their embedded capture date. |
| `pback fix-export-names` | One-time: rename files an earlier export named in UTC. |
| `pback install-agent` | Write (and optionally start) the launchd agent. |
| `pback uninstall-agent` | Stop the agent and remove its plist. |

Options:

| Option | Commands | Meaning |
| --- | --- | --- |
| `--dry-run` | `export`, `mirror`, `run`, `reconcile` | Show what would happen and change nothing. |
| `--notify` | `export`, `mirror`, `run` | Show macOS notifications for failures (and, for export, missing files). |
| `--from-date DATE`, `--to-date DATE` | `export` | Only items created on or after / before `DATE`, e.g. `2025-02-01`. |
| `--skip-reconcile-check` | `export` | Export into a folder that already has files even though reconcile hasn't been run. |
| `--delete` | `mirror` | Also delete files from the secondary backup location that are gone from the main one. |
| `--skip-space-check` | `mirror` | Don't refuse when the copy looks too big for the free space. |
| `--apply` | `fix-dates`, `fix-export-names` | Actually rename. Without it they only report. |
| `--videos`, `--photos` | `fix-dates` | Only that group. With neither, both. |
| `--include-other-names` | `fix-dates` | Also rename files with descriptive names (the description is lost). |
| `--undo LOG` | `fix-dates` | Reverse the renames recorded in an undo log. |
| `--every-hours N` | `install-agent` | Run interval. Default 6. |
| `--load` | `install-agent` | Also start the agent now. |

Exit status: 0 on success (and when another run holds the lock), 2 for a configuration error such as a missing setting, tool or folder, otherwise the exit status of `osxphotos` or `rsync`.

## Settings

Each setting is read from the environment first. If it is unset or empty there, it comes from the config file. Scheduled runs only see the config file.

| Setting | Required | Meaning |
| --- | --- | --- |
| `PHOTO_BACKER_UPPER_MAIN_DIR` | yes | The main backup location, e.g. `/Volumes/Backup/Photos` or `~/Pictures/Backup`. Must already exist. |
| `PHOTO_BACKER_UPPER_SECONDARY_DIR` | for `mirror` | The secondary backup location, e.g. `/Volumes/External/Photos`. |
| `PHOTO_BACKER_UPPER_NAS_URL` | no | A network share to mount when the main backup location isn't found, e.g. `smb://user@server/Share`. `afp://` and `nfs://` work too, and the server doesn't have to be a NAS. Don't put a password in it; the keychain supplies it. |
| `PHOTO_BACKER_UPPER_LIBRARY` | no | Path to a Photos library. Default: the system Photos library. |
| `PHOTO_BACKER_UPPER_STATE_DIR` | no | Where pback keeps its state. Default `~/.local/state/photo-backer-upper`. |
| `PHOTO_BACKER_UPPER_CONFIG` | no | Path of the config file. Environment only. Default `~/.config/photo-backer-upper/config`. |

The config file is plain `KEY=VALUE` lines:

- Blank lines and lines starting with `#` are ignored. A `#` later in a line is not a comment; it becomes part of the value.
- A leading `export ` is allowed, so lines can be copied from `~/.zshrc`.
- One pair of matching quotes around the value is removed. Values with spaces don't need quotes.
- `~` at the start of the main, secondary, state and config paths is expanded. It is not expanded in `PHOTO_BACKER_UPPER_LIBRARY`, and variables like `$HOME` are never expanded.

## State folder

Everything pback remembers is in the state folder on the Mac (`~/.local/state/photo-backer-upper` by default). Nothing is stored in the backup locations except the photos.

pback replaces its own state files whole: it writes a temporary file beside the real one and renames it into place, so a crash or full disk part-way through leaves the previous version, never a cut-short skip list. The exceptions are the undo logs, which are appended one line per rename as it happens.

| File | Written by | Purpose |
| --- | --- | --- |
| `export.db` | osxphotos | Which library items have been exported where, plus a history row for every file on every run. |
| `skip-uuids.txt` | `reconcile` (rewritten each time), `fix-export-names` (adds to it) | Library items export must skip. |
| `export-report.db` | osxphotos, during `export` | One row per file from the latest osxphotos pass, replaced every pass. pback reads it for file errors. |
| `skip-uuids.merged.txt` | `export` | `skip-uuids.txt` plus the unavailable Live Photos, rebuilt each run. |
| `live-unavailable-uuids.txt` | `export` | Live Photos exported without their video. |
| `missing-alerts.json` | `export` | When each currently missing file was last alerted. |
| `reconcile-unconfirmed.tsv` | `reconcile` | Library items whose name matched a file in the backup but couldn't be confirmed. |
| `fix-dates-report-*.tsv`, `fix-export-names-report-*.tsv` | the fix commands | The full plan from each run. |
| `fix-dates-*.tsv`, `fix-export-names-*.tsv` | the fix commands with `--apply` | Undo logs: old path, tab, new path. |
| `pback.lock` | every command except the agent ones | The run lock: the PID of the run holding it. |
| `logs/export.log` | launchd | Output of every scheduled run. |

## Tools for existing backups

If the main backup location already holds photos from before pback (copied by hand, or by another tool), these bring them into line so pback doesn't export them again. Run them in this order: `fix-dates`, then `reconcile`, then start exporting. They take the same run lock as `export`, so they exit straight away if an export is running, and a scheduled export that comes due while they run skips that turn. If one runs for more than 4 hours, scheduled runs start sending "may be stuck" notifications.

### fix-dates

Reconcile recognises existing files by their capture-time name, so files named any other way are invisible to it. `pback fix-dates` renames files in the main backup location to `YYYY-MM-DD_HH-MM-SS.ext` using the date embedded in each file. It needs `exiftool`.

```sh
pback fix-dates                    # report only
pback fix-dates --apply            # rename, writing an undo log
pback fix-dates --videos --apply   # or --photos: one group at a time
pback fix-dates --undo LOG
```

- **Videos** (`.mov`, `.mp4`, `.m4v`): renamed when the name is date-style, `mod_`-prefixed or camera-style and differs from the QuickTime `Keys:CreationDate`. A video with only a `QuickTime:CreateDate` (UTC) is counted as approximate and left alone.
- **Photos** (`.jpg`, `.jpeg`, `.heic`, `.png`, `.tif`, `.tiff`, `.gif`, `.webp`, `.avif`): only camera-style names (`IMG_1234`, `DSC_0001`, `PXL_...`, `100_0789` and similar) are renamed, from `DateTimeOriginal`, or `CreateDate` when there is no `DateTimeOriginal`. Photos already named by date, and `mod_` images, are left alone.
- **Folders:** a file in a folder named exactly `YYYY-MM` also moves to the folder for its corrected month. A file in any other folder, such as `2005-01 Family Trip`, is renamed in place. Files loose at the top of the main backup location are not touched.
- **Left alone:** descriptive names like `2007 Family Reunion 023.jpg` (unless `--include-other-names`), files with no usable embedded date or a date before 1990, empty files, hidden files, other file types, and any file whose new name would sit beside an existing file of the same size (a probable duplicate; reported, not renamed).
- A new name that is taken by a file of a different size gets a ` (1)`, ` (2)`... suffix.
- With `--apply`, each rename is checked again just before it happens. If the file has gone or its new name has been taken since the scan, it is skipped and listed, never overwritten. Run `fix-dates` again to pick those up.
- osxphotos' edited copies, named `<date>_edited` or `<date>_edited (N)`, are never touched. Other files `pback export` wrote are not recognised as such: a video named `<date>.mov` whose embedded date differs from its name would be renamed, and the next export would copy it in again under its old name. `fix-dates` is meant for files that were there before exporting started.

The summary goes to the terminal and the full plan to a report file in the state folder. `--undo` puts each file back if the new name still exists and the old one doesn't. It doesn't mount a network share for you.

### reconcile

`pback reconcile` reads the whole Photos library (`osxphotos query --json`) and the main backup location, and writes `skip-uuids.txt`: the library items that are already backed up. It needs `osxphotos` and `exiftool`, and never modifies the backup.

A library item is treated as already backed up only if both of these hold:

1. A file in the main backup location is named with the item's capture time (`YYYY-MM-DD_HH-MM-SS`, optionally followed by `-N` or ` (N)`, any extension, in any folder). Both the item's `date_original` and its `date` are tried.
2. The file's metadata agrees with the library item. Camera model, sub-second capture time and (for videos) duration are compared wherever both sides have a value. Any one of them differing rules the file out. A different aspect ratio is accepted only if the camera model or sub-second time agrees. At least one thing must agree; if there is nothing to compare, the item is not confirmed.

Each file can vouch for only one library item. Items with a name match but no confirmed file are listed in `reconcile-unconfirmed.tsv` and will be exported, which at worst makes a duplicate with a ` (1)` suffix.

Each run replaces `skip-uuids.txt` from scratch, including anything `fix-export-names` added to it.

### fix-export-names

An earlier version of export named files from `{created}`. `pback fix-export-names` goes through the files recorded in `export.db`, finds those whose name is the library's `date` where the [current rule](#file-names) gives a different time, and renames them, moving them to the right month folder and keeping any suffix such as `_edited` or ` (1)`. It never overwrites; a taken name gets the next free ` (N)`.

Without `--apply` it only writes a report. With it, each rename is written to an undo log in the same format as `fix-dates`, so `pback fix-dates --undo LOG` reverses it, and the renamed items are added to `skip-uuids.txt` so export doesn't recreate them under their old names.

## The launcher app

`launcher/build.sh` compiles `PhotoBackerUpper.swift` and signs it with the hardened runtime and the Photos entitlement.

| Variable | Default | Meaning |
| --- | --- | --- |
| `SIGN_IDENTITY` | `-` (ad hoc) | Code signing identity. |
| `APP_PATH` | `~/Applications/Photo Backer Upper.app` | Where to build. Anything already there is deleted first. |

`pback install-agent` only looks for the app at the default path.

### Releasing the launcher

`launcher/release.sh` builds the app into `build/release/`, signs it with a Developer ID certificate, has Apple notarize it, staples the ticket, and writes `build/release/PhotoBackerUpper.zip`.

One-time setup:

1. A "Developer ID Application" certificate in your keychain.
2. Notarization credentials saved under a keychain profile: `xcrun notarytool store-credentials pback-notary`

```sh
SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" sh launcher/release.sh
```

Set `NOTARY_PROFILE` to use a profile name other than `pback-notary`.

## Development

```sh
uv run pytest                  # tests
uv run ruff format --check .   # formatting (drop --check to reformat)
uv run ruff check .            # lint
```

Once per clone, enable the pre-commit hook: `git config core.hooksPath .githooks`. Before each
commit it runs the three checks above and refuses the commit if any fails; it never rewrites files.
