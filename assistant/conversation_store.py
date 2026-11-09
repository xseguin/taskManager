"""Historique conversationnel court (contexte pour « décale-la à lundi »)."""
from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Literal


class ConversationStore:
    def __init__(self, conn: sqlite3.Connection, clock: Callable[[], datetime],
                 window: timedelta = timedelta(minutes=30), max_messages: int = 20):
        self._conn = conn
        self._clock = clock
        self._window = window
        self._max = max_messages
        self._lock = threading.Lock()

    def add(self, role: Literal["user", "assistant"], content: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversation (role, content, at) VALUES (?, ?, ?)",
                (role, content, self._clock().isoformat()),
            )

    def recent(self) -> list[dict]:
        since = (self._clock() - self._window).isoformat()
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content, at FROM conversation ORDER BY id DESC LIMIT ?",
                (self._max,),
            ).fetchall()
        msgs = [{"role": r, "content": c} for r, c, at in reversed(rows)
                if datetime.fromisoformat(at) >= datetime.fromisoformat(since)]
        while msgs and msgs[0]["role"] != "user":
            msgs.pop(0)
        return msgs

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM conversation")
