# Backlog

Not part of the MVP.

- **Google redundancy.** Drive for desktop's "Back up to Google Photos" folder sync ended August 10, 2026. The only supported route into Google Photos is the website's "Back up folders" (needs a browser tab open on a computer). Plain Google Drive copy from the NAS (Synology Cloud Sync) still works but won't appear in the Photos app. Revisit later.
- **Replace archive JPGs with HEIC originals** where the Photos library has the original (pre-iCloud photos have none and stay JPG). Build on the reconcile matching; move replaced JPGs to a quarantine folder, never delete. Report first.
- **launchd automation**: `pback install-agent` is written (scheduled `export`, NAS auto-mount, lock, notifications) but not yet tested under launchd; macOS privacy permissions (Photos, Full Disk Access) for the launchd-run executable are the open risk. Still to do: `mirror` when the secondary drive mounts (`StartOnMount`), never with `--delete` unattended.
- **Bigger secondary drive.** The archive (about 2.1 TiB) no longer fits the 1 TB external drive; `mirror` refuses to start. Mirror stays off until a larger drive is in place.
- **Missing originals.** A few library items (3 videos, 9 edited Live Photos) never download; export reports them as missing and retries each run.
- **Wife's Mac**: run the same CLI there with its own local state, writing to the same main archive.
- **Approximate-date videos** (UTC `CreateDate` only): `fix-dates` lists but doesn't rename them. Could resolve them by matching the library item and using its local time.

- **`mod_` images (about 4,900).** Mostly derived files (collages, filtered images made for Instagram, screenshots) with no capture date. Their `mod_` date is often the backup date, not the creation date, so it can't be trusted for matching or renaming. Left alone for now. Matching them to library items would need content or perceptual hashing (a drag-out JPG isn't byte-identical to the library original, so a plain hash won't do). Until then, some library screenshots may already exist in the archive under `mod_` names and get exported again as duplicates.

- **Items with a bogus `date_original` (about 170).** Old files (e.g. some Fuji JPGs) whose EXIF time is an import date: their `date` is the right instant but the timezone is unknown, so they're named in UTC and can land on the wrong day or month. Could be fixed by assuming the library owner's timezone at the time, or by hand.
- **Probable duplicates from recovery.** About 330 `(1)` files were added when the reconcile audit was corrected (screenshots and PNGs that couldn't be verified). Review and quarantine, never delete.
