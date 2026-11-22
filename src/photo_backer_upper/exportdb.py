"""Reads from osxphotos' export database (`export.db` in the state folder).

osxphotos writes it during `export`; pback only reads it, through a read-only connection.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

EXPORT_DB_FILE = "export.db"


@contextmanager
def connect(export_db: Path) -> Iterator[sqlite3.Connection]:
    """A read-only connection to the export database, closed on leaving the `with` block."""
    con = sqlite3.connect(f"file:{export_db}?mode=ro", uri=True)
    try:
        yield con
    finally:
        con.close()


def missing_since(con: sqlite3.Connection, since_utc: str) -> list[tuple[str, str]]:
    """(uuid, archive path) for each file export recorded as missing at or after `since_utc`.

    `since_utc` is "YYYY-MM-DD HH:MM:SS", the same format osxphotos' history table uses.
    """
    return con.execute(
        "select distinct hp.uuid, hp.filepath_normalized from history h "
        "join history_path hp on hp.id = h.filepath_id "
        "where h.action = 'missing' and h.datetime >= ?",
        (since_utc,),
    ).fetchall()


def stills(con: sqlite3.Connection) -> set[str]:
    """UUIDs of the items that have an exported file other than a `.mov`."""
    return {
        uuid
        for uuid, path in con.execute("select uuid, filepath_normalized from export_data")
        if not path.lower().endswith(".mov")
    }


def exported_files(con: sqlite3.Connection) -> list[tuple[str, str]]:
    """(uuid, archive path) for every file export has recorded, in the order it recorded them."""
    return con.execute("select uuid, filepath from export_data order by id").fetchall()
