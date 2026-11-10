"""Chargement de la configuration du plugin depuis l'environnement."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class PluginConfig:
    todoist_token: str
    allowed_user_ids: tuple[str, ...]
    owner_chat_id: str
    timezone: ZoneInfo
    db_path: Path


REQUIRED = ("TODOIST_API_TOKEN", "TELEGRAM_ALLOWED_USERS")


def load_config(env: Mapping[str, str], data_dir: Path) -> PluginConfig:
    missing = [k for k in REQUIRED if not env.get(k, "").strip()]
    if missing:
        raise ConfigError(f"Variables manquantes : {', '.join(missing)}")
    users = tuple(u.strip() for u in env["TELEGRAM_ALLOWED_USERS"].split(",") if u.strip())
    if not users:
        raise ConfigError("TELEGRAM_ALLOWED_USERS ne contient aucun identifiant")
    try:
        tz = ZoneInfo(env.get("TIMEZONE") or "Europe/Paris")
    except Exception as e:
        raise ConfigError("TIMEZONE invalide") from e
    return PluginConfig(
        todoist_token=env["TODOIST_API_TOKEN"].strip(),
        allowed_user_ids=users,
        owner_chat_id=users[0],
        timezone=tz,
        db_path=Path(data_dir) / "assistant.db",
    )
