"""Parcours vocal : carte de transcription, validation, confirmation de réception."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from typing import Callable
from uuid import uuid4

from assistant.hermes.ports import Button, Messenger
from assistant.hermes.prompt import as_task
from assistant.hermes.relay import Relay

EXPIRED = "Ce vocal a expiré ou a déjà été traité."
NOT_UNDERSTOOD = "Je n'ai pas compris, tu peux répéter ou l'écrire ?"
FAILED = "⚠️ La transcription a échoué."


class TranscriptionError(Exception):
    pass


@dataclass
class _Entry:
    chat_id: str
    created: datetime
    text: str = ""
    audio: bytes | None = None  # conservé tant que la transcription n'a pas réussi
    message_id: int = 0
    state: str = "pending"  # pending / waiting / done
    is_task: bool = False
    injected: str = ""
    waiting_since: datetime | None = None


class VoiceFlow:
    def __init__(self, messenger: Messenger, relay: Relay,
                 transcribe: Callable[[bytes], str], clock: Callable[[], datetime],
                 card_ttl: timedelta = timedelta(hours=24),
                 confirm_timeout: timedelta = timedelta(seconds=180)):
        self._messenger = messenger
        self._relay = relay
        self._transcribe = transcribe
        self._clock = clock
        self._ttl = card_ttl
        self._confirm_timeout = confirm_timeout
        self._entries: dict[str, _Entry] = {}

    # --- cartes -----------------------------------------------------------

    @staticmethod
    def _head(text: str) -> str:
        return f"🎙️ « {escape(text, quote=False)} »"

    def _new_key(self) -> str:
        return uuid4().hex[:10]

    async def _transcribe_and_show(self, chat_id: str, audio: bytes) -> None:
        try:
            text = await asyncio.to_thread(self._transcribe, audio)
        except TranscriptionError:
            key = self._new_key()
            entry = _Entry(chat_id, self._clock(), audio=audio, state="failed")
            self._entries[key] = entry
            entry.message_id = await self._messenger.send(
                chat_id, FAILED, [Button("🔁 Réessayer", f"ta:v:again:{key}")])
            return
        text = text.strip()
        if not text:
            await self._messenger.send(chat_id, NOT_UNDERSTOOD)
            return
        key = self._new_key()
        entry = _Entry(chat_id, self._clock(), text=text)
        self._entries[key] = entry
        buttons = [Button("💬 Envoyer", f"ta:v:send:{key}"),
                   Button("📝 Créer une tâche", f"ta:v:task:{key}"),
                   Button("✖️ Annuler", f"ta:v:cancel:{key}")]
        entry.message_id = await self._messenger.send(chat_id, self._head(text), buttons)

    async def on_voice(self, chat_id: str, audio: bytes) -> None:
        await self._transcribe_and_show(chat_id, audio)

    async def on_button(self, chat_id: str, message_id: int, verb: str, key: str) -> None:
        entry = self._entries.get(key)
        wanted = "failed" if verb == "again" else "pending"
        # Le test d'état et la transition sont synchrones (aucun await entre les deux) :
        # un double clic ne peut donc déclencher qu'un seul traitement.
        if entry is None or entry.chat_id != chat_id or entry.state != wanted:
            await self._messenger.send(chat_id, EXPIRED)
            return
        if verb == "again":
            del self._entries[key]
            await self._transcribe_and_show(chat_id, entry.audio or b"")
            return
        if verb == "cancel":
            entry.state = "done"
            await self._edit(entry, "✖️ Annulé")
            return
        if verb not in ("send", "task"):
            await self._messenger.send(chat_id, EXPIRED)
            return
        entry.is_task = verb == "task"
        entry.injected = as_task(entry.text) if entry.is_task else entry.text
        entry.state = "waiting"
        entry.waiting_since = self._clock()
        if self._relay.deliver(chat_id, entry.injected):
            await self._edit(entry, "⏳ Transmis à Hermes…")
        else:
            entry.state = "done"
            await self._edit(entry, "👉 Réponds <b>ok</b> pour l'envoyer à Hermes.")

    async def _edit(self, entry: _Entry, status: str) -> None:
        await self._messenger.edit(entry.chat_id, entry.message_id,
                                   f"{self._head(entry.text)}\n\n{status}")

    # --- confirmation / expirations ----------------------------------------

    async def on_llm_call(self, user_message: str) -> None:
        for entry in list(self._entries.values()):
            if entry.state == "waiting" and entry.injected in user_message:
                entry.state = "done"
                await self._edit(entry, "📝 Reçu par Hermes comme tâche" if entry.is_task
                                 else "✅ Reçu par Hermes")
                return

    async def tick(self) -> None:
        now = self._clock()
        for key, entry in list(self._entries.items()):
            if entry.state == "waiting" and now - entry.waiting_since > self._confirm_timeout:
                entry.state = "done"
                self._relay.arm_ok(entry.chat_id, entry.injected)
                await self._edit(entry, "⚠️ Hermes n'a pas confirmé la réception. "
                                        "Réponds <b>ok</b> pour l'envoyer.")
        for key, entry in list(self._entries.items()):
            if now - entry.created > self._ttl:
                del self._entries[key]
