"""Vérifie qu'une proposition est exécutable avant de l'afficher."""
from __future__ import annotations

from assistant.models import Action, ActionKind
from assistant.todoist_client import TaskNotFound, TodoistError, TodoistPort


def validate_actions(actions: list[Action], todoist: TodoistPort) -> list[str]:
    errors: list[str] = []
    projects: set[str] | None = None
    for i, a in enumerate(actions, 1):
        try:
            if a.project is not None:
                if projects is None:
                    projects = {p.name.casefold() for p in todoist.list_projects()}
                if a.project.casefold() not in projects:
                    errors.append(f"Action {i} : le projet « {a.project} » n'existe pas.")
            if a.kind == ActionKind.CREATE:
                continue
            task = todoist.get_task(a.task_id)
        except TaskNotFound:
            task = None
        except TodoistError as e:
            errors.append(f"Action {i} : Todoist indisponible ({e}).")
            continue
        if task is None:
            errors.append(f"Action {i} : la tâche {a.task_id} est introuvable.")
            continue
        a.task_label = task.content
        if a.kind == ActionKind.COMPLETE and task.is_completed:
            errors.append(f"Action {i} : la tâche « {task.content} » est déjà terminée.")
        if a.kind == ActionKind.REOPEN and not task.is_completed:
            errors.append(f"Action {i} : la tâche « {task.content} » n'est pas terminée.")
    return errors
