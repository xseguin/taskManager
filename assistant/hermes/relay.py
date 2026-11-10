"""Relais vers Hermes : réécriture des messages de l'utilisateur (`/t`, correction de proposition)."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Callable

from assistant.hermes.prompt import TASK_MARKER, as_task, edit_request

EDIT_TTL = timedelta(minutes=10)


class Relay:
    def __init__(self, allowed_user_ids: tuple[str, ...], clock: Callable[[], datetime],
                 edit_ttl: timedelta = EDIT_TTL):
        self._allowed = tuple(allowed_user_ids)
        self._clock = clock
        self._edit_ttl = edit_ttl
        self._lock = threading.Lock()
        # chat_id -> (proposal_id, résumé, armé à)
        self._edit: dict[str, tuple[str, str, datetime]] = {}

    def arm_edit(self, chat_id: str, proposal_id: str, summary: str) -> None:
        with self._lock:
            self._edit[chat_id] = (proposal_id, summary, self._clock())

    def drop_edit(self, proposal_id: str) -> None:
        """La proposition est validée, annulée ou remplacée : sa correction n'est plus attendue."""
        with self._lock:
            for chat_id, armed in list(self._edit.items()):
                if armed[0] == proposal_id:
                    del self._edit[chat_id]

    def rewrite(self, user_id: str | None, chat_id: str, text: str,
                synthetic: bool = False) -> str | None:
        """`synthetic` : transcription d'un vocal validé par bouton. Elle peut servir de correction
        à une proposition (« Modifier » puis vocal), mais n'est jamais lue comme une commande `/t`."""
        if user_id is None or user_id not in self._allowed:
            return None
        with self._lock:
            edit = self._edit.pop(chat_id, None)
        if edit is not None and self._clock() - edit[2] <= self._edit_ttl:
            correction = text.removeprefix(TASK_MARKER) if synthetic else text
            return edit_request(edit[0], edit[1], correction)
        if synthetic:
            return None
        stripped = text.strip()
        if stripped.startswith("/t "):
            body = stripped[3:].strip()
            if body:
                return as_task(body)
        return None
