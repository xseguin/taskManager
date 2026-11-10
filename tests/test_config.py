import pytest

from assistant.config import ConfigError, load_config


def test_load_config_reads_token_users_and_db(tmp_path):
    c = load_config({"TODOIST_API_TOKEN": "tok", "TELEGRAM_ALLOWED_USERS": " 42 , 7 "}, tmp_path)
    assert c.todoist_token == "tok"
    assert c.allowed_user_ids == ("42", "7")
    assert c.owner_chat_id == "42"
    assert c.db_path == tmp_path / "assistant.db"
    assert c.timezone.key == "Europe/Paris"


def test_load_config_missing_values(tmp_path):
    with pytest.raises(ConfigError, match="TODOIST_API_TOKEN.*TELEGRAM_ALLOWED_USERS"):
        load_config({}, tmp_path)
