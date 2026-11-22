"""The Photos library, as `osxphotos query` reports it."""

import json
import subprocess


def load_library(library: str | None = None) -> list[dict]:
    """Every library item as a dict from `osxphotos query --json`.

    `library` is the path of a Photos library to read instead of the system one.
    """
    cmd = ["osxphotos", "query", "--json"]
    if library:
        cmd += ["--db", library]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    photos = json.loads(out)
    # Same filter as `export`: ignore items merely "Shared with You" that weren't saved to the
    # library.
    return [p for p in photos if not (p.get("syndicated") and not p.get("saved_to_library"))]
