"""Boucle d'outils Claude : transforme une demande en proposition, réponse ou question."""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from assistant.agent.prompt import build_system_prompt
from assistant.agent.tools import READ_TOOLS, TOOLS, WRITE_TOOLS, action_from_tool, run_read_tool
from assistant.agent.validation import validate_actions
from assistant.conversation_store import ConversationStore
from assistant.models import (Action, AgentFailure, AgentResult, Answer, Clarification, Proposal,
                              ProposalDraft)
from assistant.todoist_client import Project, TodoistError, TodoistPort

log = logging.getLogger(__name__)

MAX_TURNS = 6
TOO_COMPLEX = "Demande trop complexe, reformule-la."


def describe_actions(actions: list[Action]) -> str:
    return "\n".join(
        f"- {a.kind.value} : " + ", ".join(f"{k}={v}" for k, v in
                                           a.model_dump(exclude_none=True, exclude={"kind"}).items())
        for a in actions)


def _block_to_dict(b) -> dict:
    if b.type == "tool_use":
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
    return {"type": "text", "text": b.text}


class Agent:
    def __init__(self, client, todoist: TodoistPort, conversation: ConversationStore, model: str,
                 clock: Callable[[], datetime], project_cache_ttl: timedelta = timedelta(minutes=10),
                 tz: ZoneInfo = ZoneInfo("Europe/Paris")):
        self._client = client
        self._todoist = todoist
        self._conversation = conversation
        self._model = model
        self._clock = clock
        self._ttl = project_cache_ttl
        self._tz = tz
        self._projects: list[Project] | None = None
        self._projects_at: datetime | None = None

    def _project_list(self) -> list[Project]:
        now = self._clock()
        if self._projects is None or now - self._projects_at >= self._ttl:
            try:
                self._projects = self._todoist.list_projects()
                self._projects_at = now
            except TodoistError as e:
                log.warning("liste des projets indisponible : %s", e)
                return self._projects or []
        return self._projects

    def run(self, text: str, editing: Proposal | None = None) -> AgentResult:
        user_text = text
        if editing is not None:
            user_text = (f"Voici la proposition actuelle que je veux modifier :\n"
                         f"{describe_actions(editing.actions)}\n\n"
                         f"Modification demandée : {text}\n"
                         f"Propose la version corrigée complète.")
        result = self._loop(user_text)
        self._conversation.add("user", user_text)
        self._conversation.add("assistant", self._summary(result))
        return result

    @staticmethod
    def _summary(result: AgentResult) -> str:
        match result:
            case ProposalDraft(actions):
                return "J'ai proposé :\n" + describe_actions(actions)
            case Answer(t):
                return t
            case Clarification(q, opts):
                return q + ("\n" + "\n".join(f"- {o}" for o in opts) if opts else "")
            case AgentFailure(m):
                return m

    def _loop(self, user_text: str) -> AgentResult:
        messages = self._conversation.recent() + [{"role": "user", "content": user_text}]
        system = build_system_prompt(self._clock(), self._project_list(), self._tz)
        corrected = False
        for _ in range(MAX_TURNS):
            r = self._client.messages.create(model=self._model, max_tokens=1024, system=system,
                                             tools=TOOLS, messages=messages)
            u = r.usage
            log.info("claude tokens in=%s out=%s cache_read=%s cache_write=%s",
                     u.input_tokens, u.output_tokens, getattr(u, "cache_read_input_tokens", 0),
                     getattr(u, "cache_creation_input_tokens", 0))
            calls = [b for b in r.content if b.type == "tool_use"]
            for c in calls:
                if c.name == "ask_clarification":
                    return Clarification(c.input.get("question", ""), list(c.input.get("options") or []))
            writes = [c for c in calls if c.name in WRITE_TOOLS]
            if writes:
                actions, errors = [], {}
                for c in writes:
                    try:
                        actions.append(action_from_tool(c.name, c.input))
                    except ValueError as e:
                        errors[c.id] = str(e)
                if not errors:
                    validation = validate_actions(actions, self._todoist)
                    if not validation:
                        return ProposalDraft(actions)
                    errors = {writes[0].id: " ".join(validation)}
                if corrected:
                    return AgentFailure("Je n'ai pas réussi à préparer une action valide : "
                                        + " ".join(errors.values()))
                corrected = True
                results = []
                for c in calls:
                    if c.id in errors:
                        results.append({"type": "tool_result", "tool_use_id": c.id,
                                        "content": errors[c.id], "is_error": True})
                    elif c.name in READ_TOOLS:
                        results.append({"type": "tool_result", "tool_use_id": c.id,
                                        "content": run_read_tool(c.name, c.input, self._todoist)})
                    else:
                        results.append({"type": "tool_result", "tool_use_id": c.id,
                                        "content": "Proposition non retenue : corrige l'ensemble des actions et repropose-les toutes."})
                messages = messages + [
                    {"role": "assistant", "content": [_block_to_dict(b) for b in r.content]},
                    {"role": "user", "content": results}]
                continue
            if calls:
                results = [{"type": "tool_result", "tool_use_id": c.id,
                            "content": run_read_tool(c.name, c.input, self._todoist)} for c in calls]
                messages = messages + [
                    {"role": "assistant", "content": [_block_to_dict(b) for b in r.content]},
                    {"role": "user", "content": results}]
                continue
            return Answer("".join(b.text for b in r.content if b.type == "text").strip() or "…")
        return AgentFailure(TOO_COMPLEX)
