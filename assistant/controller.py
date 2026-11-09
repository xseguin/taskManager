"""Cœur du bot, indépendant de Telegram : entrées → réponses à afficher."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from html import escape
from uuid import uuid4

import anthropic

from assistant.agent.agent import Agent
from assistant.executor import Executor
from assistant.models import (AgentFailure, Answer, Clarification, Proposal, ProposalDraft,
                              ProposalStatus)
from assistant.pending_store import PendingStore
from assistant.render import (Button, proposal_buttons, render_proposal, render_results,
                              retry_remaining_button)
from assistant.transcriber import Transcriber, TranscriptionError

log = logging.getLogger(__name__)

EXPIRED_MSG = "Cette proposition est expirée ou déjà traitée."


@dataclass
class Reply:
    text: str
    buttons: list[Button] = field(default_factory=list)
    edit_message_id: int | None = None
    proposal_id: str | None = None


class Controller:
    def __init__(self, allowed_user_id: int, transcriber: Transcriber, agent: Agent,
                 store: PendingStore, executor: Executor, max_voice_seconds: int = 300):
        self._allowed = allowed_user_id
        self._transcriber = transcriber
        self._agent = agent
        self._store = store
        self._executor = executor
        self._max_voice = max_voice_seconds
        # Entrées en échec gardées en mémoire pour « Réessayer » : clé → (type, contenu)
        self._retry_inputs: dict[str, tuple[str, bytes | str]] = {}

    # --- points d'entrée ---------------------------------------------------
    def _authorized(self, user_id: int) -> bool:
        if user_id != self._allowed:
            log.warning("message ignoré d'un utilisateur non autorisé : %s", user_id)
            return False
        return True

    def handle_text(self, user_id: int, text: str) -> list[Reply]:
        if not self._authorized(user_id):
            return []
        return self._process_text(text)

    def handle_voice(self, user_id: int, audio: bytes, duration_s: int) -> list[Reply]:
        if not self._authorized(user_id):
            return []
        if duration_s > self._max_voice:
            return [Reply("🎙️ Vocal trop long (5 min max). Tu peux le découper ?")]
        return self._process_voice(audio)

    def handle_callback(self, user_id: int, data: str, message_id: int) -> list[Reply]:
        if not self._authorized(user_id):
            return []
        verb, _, arg = data.partition(":")
        match verb:
            case "ok":
                return self._after_execute(self._executor.execute(arg), arg, message_id)
            case "retry":
                return self._after_execute(self._executor.execute(arg, retry=True), arg, message_id)
            case "cancel":
                if not self._store.transition(arg, {ProposalStatus.PENDING, ProposalStatus.EDITING},
                                              ProposalStatus.CANCELLED):
                    return self._refused(arg, message_id)
                return [Reply(render_results(self._store.get(arg)), edit_message_id=message_id)]
            case "edit":
                previous = self._store.get_editing()
                if previous and previous.id != arg:
                    self._store.transition(previous.id, {ProposalStatus.EDITING}, ProposalStatus.PENDING)
                if not self._store.transition(arg, {ProposalStatus.PENDING}, ProposalStatus.EDITING):
                    return self._refused(arg, message_id)
                return [Reply("✏️ Que veux-tu changer ?")]
            case "again":
                entry = self._retry_inputs.get(arg)
                if entry is None:
                    return [Reply("Rien à réessayer (le bot a peut-être redémarré). Renvoie ton message.")]
                kind, payload = entry
                if kind == "voice":
                    return self._process_voice(payload, key=arg)
                return self._process_text(payload, key=arg)
        return [Reply(EXPIRED_MSG)]

    # --- traitements -------------------------------------------------------
    def _remember(self, kind: str, payload, key: str | None) -> str:
        key = key or uuid4().hex[:10]
        self._retry_inputs[key] = (kind, payload)
        return key

    def _process_voice(self, audio: bytes, key: str | None = None) -> list[Reply]:
        try:
            text = self._transcriber.transcribe(audio, "voice.ogg")
        except TranscriptionError:
            key = self._remember("voice", audio, key)
            return [Reply("⚠️ La transcription a échoué.", [Button("🔁 Réessayer", f"again:{key}")])]
        if key:
            self._retry_inputs.pop(key, None)  # l'audio n'est plus conservé
        if not text.strip():
            return [Reply("Je n'ai pas compris, tu peux répéter ou l'écrire ?")]
        return [Reply(f"🎙️ <i>« {escape(text)} »</i>")] + self._process_text(text)

    def _process_text(self, text: str, key: str | None = None) -> list[Reply]:
        editing = self._store.get_editing()
        try:
            result = self._agent.run(text, editing=editing)
        except anthropic.APIError as e:
            log.error("erreur Anthropic : %s", e)
            key = self._remember("text", text, key)
            return [Reply("⚠️ Claude ne répond pas.", [Button("🔁 Réessayer", f"again:{key}")])]
        except Exception:
            log.exception("erreur inattendue pendant le traitement")
            key = self._remember("text", text, key)
            return [Reply("⚠️ Erreur inattendue.", [Button("🔁 Réessayer", f"again:{key}")])]
        if key:
            self._retry_inputs.pop(key, None)
        if editing is not None and not isinstance(result, (ProposalDraft, Clarification)):
            # la réponse n'est pas une correction : on quitte le mode édition
            self._store.transition(editing.id, {ProposalStatus.EDITING}, ProposalStatus.PENDING)
        match result:
            case ProposalDraft(actions):
                replies = []
                if editing is not None and self._store.transition(
                        editing.id, {ProposalStatus.EDITING}, ProposalStatus.REPLACED):
                    old = self._store.get(editing.id)
                    if old.message_id is not None:
                        replies.append(Reply(render_results(old), edit_message_id=old.message_id))
                p = self._store.create(actions, text)
                replies.append(Reply(render_proposal(p), proposal_buttons(p.id), proposal_id=p.id))
                return replies
            case Answer(t):
                return [Reply(escape(t) or "…")]
            case Clarification(question, options):
                lines = [f"❓ {escape(question)}"] + [f"• {escape(o)}" for o in options]
                return [Reply("\n".join(lines))]
            case AgentFailure(message):
                return [Reply(f"⚠️ {escape(message)}")]
        return [Reply("⚠️ Réponse inattendue.")]

    def _after_execute(self, p: Proposal | None, proposal_id: str, message_id: int) -> list[Reply]:
        if p is None:
            return self._refused(proposal_id, message_id)
        buttons = [retry_remaining_button(p.id)] if p.status == ProposalStatus.PARTIAL else []
        return [Reply(render_results(p), buttons, edit_message_id=message_id)]

    def _refused(self, proposal_id: str, message_id: int) -> list[Reply]:
        replies = [Reply(EXPIRED_MSG)]
        p = self._store.get(proposal_id)
        if p is not None and p.status == ProposalStatus.EXPIRED:
            replies.insert(0, Reply(render_results(p), edit_message_id=message_id))
        return replies
