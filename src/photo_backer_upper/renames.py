"""What fix-dates and fix-export-names share: a planned rename, and the undo log they write."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Rename:
    """One file to rename from `old` to `new`. Each tool fills in only the fields it uses."""

    old: Path
    new: Path
    kind: str = ""  # fix-dates: "video" or "photo"
    reason: str = ""  # fix-dates: why the name is wrong, as written in its report
    uuid: str = ""  # fix-export-names: the library item the file belongs to


@contextmanager
def undo_log(path: Path) -> Iterator[Callable[[Rename], None]]:
    """Open the undo log at `path` for appending; yield a function that records one rename.

    Each rename is one `old<TAB>new` line in UTF-8, flushed at once so the log stays complete if
    the run is interrupted. `pback fix-dates --undo` reads this format, including logs written by
    earlier versions, so don't change it.
    """
    with open(path, "a", encoding="utf-8") as f:

        def record(rename: Rename) -> None:
            f.write(f"{rename.old}\t{rename.new}\n")
            f.flush()

        yield record
