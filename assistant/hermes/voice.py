"""Parcours vocal : carte de transcription, validation, confirmation de réception."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from typing import Callable
from uuid import uuid4

from assistant.hermes.ports import Button, Messenger, Submitter
from assistant.hermes.prompt import as_task

log = logging.getLogger("todoist-assistant")

EXPIRED = "Ce vocal a expiré ou a déjà été traité."
NOT_UNDERSTOOD = "Je n'ai pas compris, tu peux répéter ou l'écrire ?"
FAILED = "⚠️ La transcription a échoué."
SENT = "⏳ Transmis à Hermes…"
UNCONFIRMED = "⚠️ Hermes n'a pas confirmé la réception."


class TranscriptionError(Exception):
    pass


@dataclass
class _Entry:
    chat_id: str
    created: datetime
    source: str  # vocal d'origine, rendu au Submitter
    text: str = ""
    audio: bytes | None = None  # conservé tant que la transcription n'a pas réussi
    message_id: int = 0
    state: str = "pending"  # failed / pending / waiting / unconfirmed / done
    is_task: bool = False
    submitted: str = ""
    waiting_since: datetime | None = None
    resent: bool = False


class VoiceFlow:
    def __init__(self, messenger: Messenger, submitter: Submitter,
                 transcribe: Callable[[bytes], str], clock: Callable[[], datetime],
                 card_ttl: timedelta = timedelta(hours=24),
                 confirm_timeout: timedelta = timedelta(seconds=180)):
        self._messenger = messenger
        self._submitter = submitter
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

    async def _transcribe_and_show(self, chat_id: str, audio: bytes, source: str) -> None:
        try:
            text = await asyncio.to_thread(self._transcribe, audio)
        except TranscriptionError:
            key = self._new_key()
            entry = _Entry(chat_id, self._clock(), source, audio=audio, state="failed")
            self._entries[key] = entry
            entry.message_id = await self._messenger.send(
                chat_id, FAILED, [Button("🔁 Réessayer", f"ta:v:again:{key}")])
            return
        text = text.strip()
        if not text:
            await self._messenger.send(chat_id, NOT_UNDERSTOOD)
            return
        key = self._new_key()
        entry = _Entry(chat_id, self._clock(), source, text=text)
        self._entries[key] = entry
        buttons = [Button("💬 Discuter", f"ta:v:send:{key}"),
                   Button("📝 Créer une tâche", f"ta:v:task:{key}"),
                   Button("✖️ Annuler", f"ta:v:cancel:{key}")]
        entry.message_id = await self._messenger.send(chat_id, self._head(text), buttons)

    async def on_voice(self, chat_id: str, audio: bytes, source: str) -> None:
        await self._transcribe_and_show(chat_id, audio, source)

    async def on_button(self, chat_id: str, message_id: int, verb: str, key: str) -> None:
        entry = self._entries.get(key)
        wanted = {"again": "failed", "resend": "unconfirmed"}.get(verb, "pending")
        # Le test d'état et la transition sont synchrones (aucun await entre les deux) :
        # un double clic ne peut donc déclencher qu'un seul traitement.
        if (entry is None or entry.chat_id != chat_id or entry.state != wanted
                or (verb == "resend" and entry.resent)):
            await self._messenger.send(chat_id, EXPIRED)
            return
        if verb == "again":
            del self._entries[key]
            await self._transcribe_and_show(chat_id, entry.audio or b"", entry.source)
            return
        if verb == "cancel":
            entry.state = "done"
            await self._edit(entry, "✖️ Annulé")
            return
        if verb == "resend":
            entry.resent = True
        elif verb in ("send", "task"):
            entry.is_task = verb == "task"
            entry.submitted = as_task(entry.text) if entry.is_task else entry.text
        else:
            await self._messenger.send(chat_id, EXPIRED)
            return
        await self._submit(key, entry)

    async def _submit(self, key: str, entry: _Entry) -> None:
        entry.state = "waiting"
        entry.waiting_since = self._clock()
        await self._edit(entry, SENT)
        try:
            await self._submitter.submit(entry.chat_id, entry.source, entry.submitted)
        except Exception:
            log.exception("remise du vocal à Hermes en échec")
            if entry.state == "waiting":
                await self._unconfirmed(key, entry)

    async def _unconfirmed(self, key: str, entry: _Entry) -> None:
        entry.state = "unconfirmed"
        buttons = None if entry.resent else [Button("🔁 Renvoyer", f"ta:v:resend:{key}")]
        await self._edit(entry, UNCONFIRMED, buttons)

    async def _edit(self, entry: _Entry, status: str, buttons: list[Button] | None = None) -> None:
        await self._messenger.edit(entry.chat_id, entry.message_id,
                                   f"{self._head(entry.text)}\n\n{status}", buttons)

    # --- confirmation / expirations ----------------------------------------

    async def on_llm_call(self, user_message: str) -> None:
        # La transcription seule (sans marqueur) : une correction de proposition l'enveloppe autrement.
        for entry in list(self._entries.values()):
            if entry.state in ("waiting", "unconfirmed") and entry.text in user_message:
                entry.state = "done"
                await self._edit(entry, "📝 Reçu par Hermes comme tâche" if entry.is_task
                                 else "✅ Reçu par Hermes")
                return

    async def tick(self) -> None:
        now = self._clock()
        for key, entry in list(self._entries.items()):
            if entry.state == "waiting" and now - entry.waiting_since > self._confirm_timeout:
                await self._unconfirmed(key, entry)
        for key, entry in list(self._entries.items()):
            if now - entry.created > self._ttl:
                del self._entries[key]
