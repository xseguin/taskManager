"""Relais vers Hermes : réécritures de messages, repli « ok », suivi des tours humains."""
from __future__ import annotations

import threading

from assistant.hermes.ports import Injector
from assistant.hermes.prompt import as_task, edit_request


class Relay:
    def __init__(self, allowed_user_ids: tuple[str, ...], injector: Injector):
        self._allowed = tuple(allowed_user_ids)
        self._injector = injector
        self._lock = threading.Lock()
        self._human: set[str] = set()
        self._ok: dict[str, str] = {}
        self._edit: dict[str, tuple[str, str]] = {}

    def note_admission(self, chat_id: str) -> None:
        with self._lock:
            self._human.add(chat_id)

    def note_reset(self, chat_id: str) -> None:
        with self._lock:
            self._human.discard(chat_id)

    def has_human_turn(self, chat_id: str) -> bool:
        with self._lock:
            return chat_id in self._human

    def arm_ok(self, chat_id: str, text: str) -> None:
        with self._lock:
            self._ok[chat_id] = text

    def arm_edit(self, chat_id: str, proposal_id: str, summary: str) -> None:
        with self._lock:
            self._edit[chat_id] = (proposal_id, summary)

    def deliver(self, chat_id: str, text: str) -> bool:
        if self.has_human_turn(chat_id) and self._injector.inject(chat_id, text):
            return True
        self.arm_ok(chat_id, text)
        return False

    def rewrite(self, user_id: str | None, chat_id: str, text: str) -> str | None:
        if user_id is None or user_id not in self._allowed:
            return None
        stripped = text.strip()
        with self._lock:
            if stripped.casefold() == "ok":
                return self._ok.pop(chat_id, None)
            edit = self._edit.pop(chat_id, None)
        if edit is not None:
            return edit_request(edit[0], edit[1], text)
        if stripped.startswith("/t "):
            body = stripped[3:].strip()
            if body:
                return as_task(body)
        return None
