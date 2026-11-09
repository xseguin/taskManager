"""Chargement de la configuration depuis l'environnement."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    telegram_token: str
    allowed_user_id: int
    anthropic_api_key: str
    claude_model: str
    todoist_token: str
    groq_api_key: str
    timezone: ZoneInfo
    db_path: Path


REQUIRED = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_ID", "ANTHROPIC_API_KEY",
            "TODOIST_API_TOKEN", "GROQ_API_KEY")


def load_config(env: Mapping[str, str]) -> Config:
    missing = [k for k in REQUIRED if not env.get(k, "").strip()]
    if missing:
        raise ConfigError(f"Variables manquantes : {', '.join(missing)}")
    try:
        user_id = int(env["TELEGRAM_ALLOWED_USER_ID"])
    except ValueError as e:
        raise ConfigError("TELEGRAM_ALLOWED_USER_ID doit être un entier") from e
    try:
        tz = ZoneInfo(env.get("TIMEZONE") or "Europe/Paris")
    except Exception as e:
        raise ConfigError("TIMEZONE invalide") from e
    return Config(
        telegram_token=env["TELEGRAM_BOT_TOKEN"],
        allowed_user_id=user_id,
        anthropic_api_key=env["ANTHROPIC_API_KEY"],
        claude_model=env.get("CLAUDE_MODEL") or "claude-haiku-5-5",
        todoist_token=env["TODOIST_API_TOKEN"],
        groq_api_key=env["GROQ_API_KEY"],
        timezone=tz,
        db_path=Path(env.get("DB_PATH") or "./data/assistant.db"),
    )
