"""Connexion SQLite et schéma."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, status TEXT NOT NULL, data TEXT NOT NULL);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False : le plugin appelle le cœur depuis des threads (asyncio.to_thread).
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.executescript(SCHEMA)
    return conn
