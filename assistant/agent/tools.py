"""Outils exposés à Claude et conversion en actions."""
from __future__ import annotations

from pydantic import ValidationError

from assistant.models import Action, ActionKind
from assistant.todoist_client import TaskNotFound, TodoistError, TodoistPort, format_tasks_for_llm

_TASK_ID = {"task_id": {"type": "string", "description": "Identifiant de la tâche (entre crochets dans les résultats de recherche)."}}
_FIELDS = {
    "content": {"type": "string", "description": "Titre de la tâche, court et actionnable."},
    "due_string": {"type": "string", "description": 'Échéance en langage naturel compris par Todoist, en anglais de préférence (ex. "thursday 18:00", "tomorrow", "every monday").'},
    "project": {"type": "string", "description": "Nom exact d'un projet existant."},
    "priority": {"type": "integer", "minimum": 1, "maximum": 4, "description": "4 = urgente, 3 = haute, 2 = moyenne, 1 = normale."},
    "labels": {"type": "array", "items": {"type": "string"}, "description": "Étiquettes."},
    "description": {"type": "string", "description": "Notes complémentaires."},
}


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


TOOLS: list[dict] = [
    {"name": "list_projects", "description": "Liste les projets Todoist.", "input_schema": _schema({}, [])},
    {"name": "search_tasks",
     "description": "Cherche des tâches actives. À utiliser avant toute modification pour trouver le task_id.",
     "input_schema": _schema({
         "query": {"type": "string", "description": "Mots du titre."},
         "project": {"type": "string", "description": "Nom du projet."},
         "due_filter": {"type": "string", "description": 'Filtre Todoist sur l\'échéance : "today", "overdue", "7 days", "today | overdue"…'},
     }, [])},
    {"name": "get_task", "description": "Détail d'une tâche.", "input_schema": _schema(_TASK_ID, ["task_id"])},
    {"name": "propose_create_task", "description": "Propose de créer une tâche (exécutée seulement après validation de l'utilisateur).",
     "input_schema": _schema(_FIELDS, ["content"])},
    {"name": "propose_update_task", "description": "Propose de modifier une tâche existante. Ne renseigner que les champs qui changent.",
     "input_schema": _schema({**_TASK_ID, **_FIELDS}, ["task_id"])},
    {"name": "propose_complete_task", "description": "Propose de marquer une tâche comme terminée.",
     "input_schema": _schema(_TASK_ID, ["task_id"])},
    {"name": "propose_reopen_task", "description": "Propose de rouvrir une tâche terminée.",
     "input_schema": _schema(_TASK_ID, ["task_id"])},
    {"name": "propose_delete_task", "description": "Propose de supprimer définitivement une tâche.",
     "input_schema": _schema(_TASK_ID, ["task_id"])},
    {"name": "ask_clarification",
     "description": "Pose une question à l'utilisateur quand la demande est ambiguë (ex. plusieurs tâches correspondent).",
     "input_schema": _schema({
         "question": {"type": "string"},
         "options": {"type": "array", "items": {"type": "string"}},
     }, ["question"]),
     "cache_control": {"type": "ephemeral"}},
]

READ_TOOLS = {"list_projects", "search_tasks", "get_task"}
WRITE_TOOLS = {"propose_create_task", "propose_update_task", "propose_complete_task",
               "propose_reopen_task", "propose_delete_task"}

_KIND = {
    "propose_create_task": ActionKind.CREATE,
    "propose_update_task": ActionKind.UPDATE,
    "propose_complete_task": ActionKind.COMPLETE,
    "propose_reopen_task": ActionKind.REOPEN,
    "propose_delete_task": ActionKind.DELETE,
}


def action_from_tool(name: str, tool_input: dict) -> Action:
    try:
        return Action(kind=_KIND[name], **{k: v for k, v in tool_input.items() if k != "task_label"})
    except ValidationError as e:
        msgs = "; ".join(err["msg"].removeprefix("Value error, ") for err in e.errors())
        raise ValueError(f"{name} : {msgs}") from e


def run_read_tool(name: str, tool_input: dict, todoist: TodoistPort) -> str:
    try:
        if name == "list_projects":
            return "\n".join(f"- {p.name}" for p in todoist.list_projects()) or "Aucun projet."
        if name == "search_tasks":
            return format_tasks_for_llm(todoist.search_tasks(
                query=tool_input.get("query"), project=tool_input.get("project"),
                due_filter=tool_input.get("due_filter")))
        if name == "get_task":
            return format_tasks_for_llm([todoist.get_task(tool_input["task_id"])])
    except TaskNotFound:
        return f"Tâche {tool_input.get('task_id')} introuvable."
    except TodoistError as e:
        return f"Erreur Todoist : {e}"
    return f"Outil inconnu : {name}"
