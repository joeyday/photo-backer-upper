"""Replace state files all at once.

The skip list is what keeps `export` from copying thousands of already-archived photos in
again, so a crash, a full disk or a power cut part-way through writing it must never leave a
short or half-written file behind. Writing to a temporary file in the same folder and then
renaming it over the old one means a reader sees either the old file or the new one.
"""

import os
from pathlib import Path


def write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
