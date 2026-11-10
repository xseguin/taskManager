import pytest

from assistant.config import ConfigError, load_config


@pytest.fixture
def token_file(tmp_path):
    p = tmp_path / "token"
    p.write_text("  tok\n")
    return p


def test_load_config_reads_token_file_users_and_db(tmp_path, token_file):
    c = load_config({"TELEGRAM_ALLOWED_USERS": " 42 , 7 "}, tmp_path, token_file)
    assert c.todoist_token == "tok"
    assert c.allowed_user_ids == ("42", "7")
    assert c.owner_chat_id == "42"
    assert c.db_path == tmp_path / "assistant.db"
    assert c.timezone.key == "Europe/Paris"


def test_load_config_ignores_token_in_env(tmp_path, token_file):
    c = load_config({"TODOIST_API_TOKEN": "from-env", "TELEGRAM_ALLOWED_USERS": "42"},
                    tmp_path, token_file)
    assert c.todoist_token == "tok"


def test_load_config_missing_users(tmp_path, token_file):
    with pytest.raises(ConfigError, match="TELEGRAM_ALLOWED_USERS"):
        load_config({}, tmp_path, token_file)


def test_load_config_missing_token_file(tmp_path):
    missing = tmp_path / "absent"
    with pytest.raises(ConfigError, match=str(missing)):
        load_config({"TELEGRAM_ALLOWED_USERS": "42"}, tmp_path, missing)


def test_load_config_empty_token_file(tmp_path):
    empty = tmp_path / "token"
    empty.write_text(" \n")
    with pytest.raises(ConfigError, match="vide"):
        load_config({"TELEGRAM_ALLOWED_USERS": "42"}, tmp_path, empty)
