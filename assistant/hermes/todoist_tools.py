"""Outils Todoist exposés à Hermes et cartes de proposition validées au clic.

Seul `Executor.execute`, appelé depuis les boutons « Valider » / « Réessayer », écrit dans Todoist.
"""
from __future__ import annotations

import asyncio
import json

from pydantic import ValidationError

from assistant.executor import Executor
from assistant.hermes.ports import Messenger
from assistant.hermes.relay import Relay
from assistant.models import Action, ProposalStatus
from assistant.pending_store import PendingStore
from assistant.render import (proposal_buttons, render_proposal, render_results,
                              retry_remaining_button)
from assistant.todoist_client import TaskNotFound, TodoistError, TodoistPort, format_tasks_for_llm
from assistant.validation import validate_actions

EXPIRED_MSG = "Cette proposition est expirée ou déjà traitée."
EDIT_PROMPT = "✏️ Que veux-tu changer ?"

_TASK_ID = {"type": "string", "description": "Identifiant de la tâche (entre crochets dans les résultats de recherche)."}
_ACTION_PROPS = {
    "kind": {"type": "string", "enum": ["create", "update", "complete", "reopen", "delete"],
             "description": "Type d'action."},
    "task_id": _TASK_ID,
    "content": {"type": "string", "description": "Titre de la tâche, court et actionnable."},
    "due_string": {"type": "string", "description": 'Échéance en langage naturel compris par Todoist, en anglais de préférence (ex. "thursday 18:00", "tomorrow", "every monday").'},
    "project": {"type": "string", "description": "Nom exact d'un projet existant."},
    "priority": {"type": "integer", "minimum": 1, "maximum": 4, "description": "4 = urgente, 3 = haute, 2 = moyenne, 1 = normale."},
    "labels": {"type": "array", "items": {"type": "string"}, "description": "Étiquettes."},
    "description": {"type": "string", "description": "Notes complémentaires."},
}


def _schema(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": props, "required": required}}


TOOL_SCHEMAS: dict[str, dict] = {s["name"]: s for s in [
    _schema("todoist_list_projects", "Liste les projets Todoist.", {}, []),
    _schema("todoist_search_tasks",
            "Cherche des tâches actives. À utiliser avant toute modification pour trouver le task_id.",
            {"query": {"type": "string", "description": "Mots du titre."},
             "project": {"type": "string", "description": "Nom du projet."},
             "due_filter": {"type": "string", "description": 'Filtre Todoist sur l\'échéance : "today", "overdue", "7 days", "today | overdue"…'}},
            []),
    _schema("todoist_get_task", "Détail d'une tâche.", {"task_id": _TASK_ID}, ["task_id"]),
    _schema("todoist_propose_actions",
            "Propose une ou plusieurs actions Todoist (créer, modifier, terminer, rouvrir, supprimer) "
            "sur une seule carte. Rien n'est exécuté avant la validation de l'utilisateur par bouton. "
            "Pour modifier une proposition existante, renseigner replaces_proposal_id.",
            {"actions": {"type": "array", "minItems": 1,
                         "items": {"type": "object", "properties": _ACTION_PROPS, "required": ["kind"]},
                         "description": "Actions à valider ensemble. Pour update, ne renseigner que les champs qui changent."},
             "replaces_proposal_id": {"type": "string",
                                      "description": "Identifiant de la proposition remplacée (lors d'une modification demandée par l'utilisateur)."}},
            ["actions"]),
]}


def run_read_tool(name: str, args: dict, todoist: TodoistPort) -> str:
    try:
        if name == "todoist_list_projects":
            return "\n".join(f"- {p.name}" for p in todoist.list_projects()) or "Aucun projet."
        if name == "todoist_search_tasks":
            return format_tasks_for_llm(todoist.search_tasks(
                query=args.get("query"), project=args.get("project"),
                due_filter=args.get("due_filter")))
        if name == "todoist_get_task":
            return format_tasks_for_llm([todoist.get_task(args["task_id"])])
    except TaskNotFound:
        return f"Tâche {args.get('task_id')} introuvable."
    except TodoistError as e:
        return f"Erreur Todoist : {e}"
    except KeyError as e:
        return f"Paramètre manquant : {e}"
    return f"Outil inconnu : {name}"


def _err(msg: str) -> str:
    return json.dumps({"error": msg}, ensure_ascii=False)


class ProposalFlow:
    def __init__(self, store: PendingStore, executor: Executor, todoist: TodoistPort,
                 messenger: Messenger, relay: Relay, owner_chat_id: str):
        self._store = store
        self._executor = executor
        self._todoist = todoist
        self._messenger = messenger
        self._relay = relay
        self._owner = owner_chat_id

    async def propose(self, args: dict) -> str:
        raw = args.get("actions")
        if not isinstance(raw, list) or not raw:
            return _err("actions : au moins une action est requise.")
        try:
            actions = [Action(**a) for a in raw]
        except (ValidationError, TypeError) as e:
            if isinstance(e, ValidationError):
                msgs = "; ".join(err["msg"].removeprefix("Value error, ") for err in e.errors())
            else:
                msgs = str(e)
            return _err(f"Proposition invalide : {msgs}")
        errors = await asyncio.to_thread(validate_actions, actions, self._todoist)
        if errors:
            return _err(" ".join(errors))
        p = self._store.create(actions, source_text="")
        mid = await self._messenger.send(self._owner, render_proposal(p), proposal_buttons(p.id))
        self._store.set_message_id(p.id, mid)
        old_id = args.get("replaces_proposal_id")
        if old_id and self._store.transition(old_id, {ProposalStatus.PENDING}, ProposalStatus.REPLACED):
            old = self._store.get(old_id)
            if old and old.message_id is not None:
                await self._messenger.edit(self._owner, old.message_id, render_results(old), None)
        return json.dumps({"status": "pending_user_validation", "proposal_id": p.id})

    async def on_button(self, chat_id: str, message_id: int, verb: str, proposal_id: str) -> None:
        if verb in ("ok", "retry"):
            p = await asyncio.to_thread(self._executor.execute, proposal_id, verb == "retry")
            if p is None:
                await self._refuse(chat_id, message_id, proposal_id)
                return
            buttons = [retry_remaining_button(p.id)] if p.status == ProposalStatus.PARTIAL else None
            await self._messenger.edit(chat_id, message_id, render_results(p), buttons)
        elif verb == "cancel":
            if self._store.transition(proposal_id, {ProposalStatus.PENDING, ProposalStatus.EDITING},
                                      ProposalStatus.CANCELLED):
                await self._messenger.edit(chat_id, message_id,
                                           render_results(self._store.get(proposal_id)), None)
            else:
                await self._refuse(chat_id, message_id, proposal_id)
        elif verb == "edit":
            p = self._store.get(proposal_id)
            if p is None or p.status != ProposalStatus.PENDING:
                await self._refuse(chat_id, message_id, proposal_id)
                return
            self._relay.arm_edit(chat_id, proposal_id, render_proposal(p))
            await self._messenger.send(chat_id, EDIT_PROMPT)
        else:
            await self._refuse(chat_id, message_id, proposal_id)

    async def _refuse(self, chat_id: str, message_id: int, proposal_id: str) -> None:
        p = self._store.get(proposal_id)
        if p is not None and p.status == ProposalStatus.EXPIRED:
            await self._messenger.edit(chat_id, message_id, render_results(p), None)
        await self._messenger.send(chat_id, EXPIRED_MSG)
