"""SQLite access for the Tatoeba corpus + review queue.

`connect()` opens a DB and applies `schema.sql` (idempotent), returning a
connection with `row_factory = sqlite3.Row` so callers get column access by name.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def schema_sql() -> str:
    return _SCHEMA_PATH.read_text(encoding="utf-8")


def connect(db_path: str | Path = ":memory:") -> sqlite3.Connection:
    """Open ``db_path`` (creating parent dirs), apply the schema, return conn."""
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(schema_sql())
    return conn
