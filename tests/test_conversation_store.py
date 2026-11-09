from datetime import timedelta

import pytest

from assistant.conversation_store import ConversationStore


@pytest.fixture
def conv(conn, clock):
    return ConversationStore(conn, clock)


def test_window_30_minutes(conv, clock):
    conv.add("user", "vieux")
    clock.advance(timedelta(minutes=31))
    conv.add("user", "récent")
    assert [m["content"] for m in conv.recent()] == ["récent"]


def test_max_20_messages(conv):
    for i in range(25):
        conv.add("user" if i % 2 == 0 else "assistant", str(i))
    r = conv.recent()
    assert len(r) <= 20 and r[0]["role"] == "user" and r[-1]["content"] == "24"


def test_clear(conv):
    conv.add("user", "x")
    conv.clear()
    assert conv.recent() == []


def test_recent_skips_empty_rows(conv):
    conv.add("user", "a")
    conv.add("assistant", "")
    conv.add("user", "b")
    assert all(m["content"] for m in conv.recent())
