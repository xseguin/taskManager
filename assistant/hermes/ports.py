"""Ports de la colle Hermes : le coeur ne dépend d'aucune bibliothèque Telegram."""
from __future__ import annotations

from typing import Protocol

from assistant.render import Button

__all__ = ["Button", "Messenger", "Submitter"]


class Messenger(Protocol):
    async def send(self, chat_id: str, text: str, buttons: list[Button] | None = None) -> int: ...

    async def edit(self, chat_id: str, message_id: int, text: str,
                   buttons: list[Button] | None = None) -> None: ...


class Submitter(Protocol):
    """Remet `text` à Hermes comme un vrai message de l'utilisateur, à la place du vocal `source`."""

    async def submit(self, chat_id: str, source: str, text: str) -> None: ...
