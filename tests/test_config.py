from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from assistant.config import ConfigError, load_config

BASE = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_ALLOWED_USER_ID": "42", "ANTHROPIC_API_KEY": "a",
        "TODOIST_API_TOKEN": "td", "GROQ_API_KEY": "g"}


def test_defaults():
    c = load_config(BASE)
    assert c.allowed_user_id == 42
    assert c.claude_model == "claude-haiku-5-5"
    assert c.timezone == ZoneInfo("Europe/Paris")
    assert c.db_path == Path("./data/assistant.db")


def test_missing_key_raises_with_name():
    env = {k: v for k, v in BASE.items() if k != "GROQ_API_KEY"}
    with pytest.raises(ConfigError, match="GROQ_API_KEY"):
        load_config(env)


def test_non_numeric_user_id_raises():
    with pytest.raises(ConfigError, match="TELEGRAM_ALLOWED_USER_ID"):
        load_config({**BASE, "TELEGRAM_ALLOWED_USER_ID": "abc"})
