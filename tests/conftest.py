from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

PARIS = ZoneInfo("Europe/Paris")


class Clock:
    """Horloge figée et avançable manuellement."""

    def __init__(self, start: datetime):
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current = self.current + delta


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 11, 9, 15, 0, tzinfo=PARIS)  # un lundi


@pytest.fixture
def clock(now) -> Clock:
    return Clock(now)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "t.db"


@pytest.fixture
def conn(db_path):
    from assistant.db import connect
    return connect(db_path)


@pytest.fixture
def store(conn, clock):
    from assistant.pending_store import PendingStore
    return PendingStore(conn, clock)
