"""SQLite connection handling."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    """Open a world database with the conventions the rest of the code assumes.

    Rows come back as ``sqlite3.Row`` so callers index by column name, and
    foreign keys are enforced (SQLite leaves them off by default, which would
    let a bad loader silently orphan contracts).
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    """Create the schema if it is not already present."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
