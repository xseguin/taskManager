"""Chargement de la configuration du plugin.

Le jeton Todoist vient d'un fichier hors de HERMES_HOME (jamais de l'environnement) : ainsi il
ne peut pas être transmis au conteneur des outils de l'agent (voir deploy/sandbox.md).
"""
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


REQUIRED = ("TELEGRAM_ALLOWED_USERS",)


def _read_token(token_file: Path) -> str:
    try:
        token = Path(token_file).read_text(encoding="utf-8").strip()
    except OSError as e:
        raise ConfigError(f"Jeton Todoist illisible : {token_file} ({type(e).__name__})") from None
    if not token:
        raise ConfigError(f"Jeton Todoist vide : {token_file}")
    return token


def load_config(env: Mapping[str, str], data_dir: Path, token_file: Path) -> PluginConfig:
    missing = [k for k in REQUIRED if not env.get(k, "").strip()]
    if missing:
        raise ConfigError(f"Variables manquantes : {', '.join(missing)}")
    token = _read_token(token_file)
    users = tuple(u.strip() for u in env["TELEGRAM_ALLOWED_USERS"].split(",") if u.strip())
    if not users:
        raise ConfigError("TELEGRAM_ALLOWED_USERS ne contient aucun identifiant")
    try:
        tz = ZoneInfo(env.get("TIMEZONE") or "Europe/Paris")
    except Exception as e:
        raise ConfigError("TIMEZONE invalide") from e
    return PluginConfig(
        todoist_token=token,
        allowed_user_ids=users,
        owner_chat_id=users[0],
        timezone=tz,
        db_path=Path(data_dir) / "assistant.db",
    )
