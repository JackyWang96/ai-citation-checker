"""sqlite-vec plumbing for the APA rules index.

Single source of truth for *how to open* a vector database. Opening a
connection without loading the extension makes every query against the vec0
virtual table fail with "no such module: vec0" — and "Python supports loading
extensions" is not the same as "this connection registered vec0". Route every
connection through `open_rules_db`.
"""
from __future__ import annotations

import sqlite3
import struct

import sqlite_vec


def open_rules_db(path: str, *, read_only: bool = False) -> sqlite3.Connection:
    """Open a rules index with the sqlite-vec extension loaded."""
    if read_only:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    else:
        db = sqlite3.connect(path, check_same_thread=False)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    return db


def pack_vector(vec: list[float]) -> bytes:
    """Serialise a float vector into the layout sqlite-vec expects."""
    return struct.pack(f"{len(vec)}f", *vec)


def vec_version() -> str:
    """Version of the loaded sqlite-vec extension (recorded in index_meta)."""
    db = sqlite3.connect(":memory:")
    try:
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        return db.execute("SELECT vec_version()").fetchone()[0]
    finally:
        db.close()
