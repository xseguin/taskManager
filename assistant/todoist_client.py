"""Accès à Todoist : seul module qui parle à l'API."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx

from assistant.models import Action


@dataclass
class Project:
    id: str
    name: str


@dataclass
class Task:
    id: str
    content: str
    project_id: str
    priority: int
    due: str | None
    labels: list[str] = field(default_factory=list)
    url: str = ""
    is_completed: bool = False


class TodoistError(Exception):
    pass


class TaskNotFound(TodoistError):
    pass


class TodoistPort(Protocol):
    def list_projects(self) -> list[Project]: ...
    def search_tasks(self, query: str | None = None, project: str | None = None,
                     due_filter: str | None = None) -> list[Task]: ...
    def get_task(self, task_id: str) -> Task | None: ...
    def create_task(self, action: Action) -> Task: ...
    def update_task(self, action: Action) -> Task: ...
    def complete_task(self, task_id: str) -> None: ...
    def reopen_task(self, task_id: str) -> None: ...
    def delete_task(self, task_id: str) -> None: ...


PRIORITY_LABEL = {4: "P1", 3: "P2", 2: "P3", 1: "P4"}


def format_tasks_for_llm(tasks: list[Task], limit: int = 30) -> str:
    if not tasks:
        return "Aucune tâche trouvée."
    lines = [
        f"[{t.id}] {t.content} — échéance : {t.due or 'aucune'} — projet : {t.project_id}"
        f" — {PRIORITY_LABEL.get(t.priority, 'P4')}" + (" — terminée" if t.is_completed else "")
        for t in tasks[:limit]
    ]
    out = "\n".join(lines)
    if len(tasks) > limit:
        out += f"\n({len(tasks) - limit} autres non affichés)"
    return out


def _flatten(pages) -> list:
    return [item for page in pages for item in page]


class TodoistClient:
    def __init__(self, token: str):
        from todoist_api_python.api import TodoistAPI
        self._api = TodoistAPI(token)

    # --- utilitaires ----------------------------------------------------
    def _call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise TaskNotFound("tâche introuvable") from e
            raise TodoistError(f"HTTP {e.response.status_code}") from e
        except httpx.HTTPError as e:
            raise TodoistError(str(e) or e.__class__.__name__) from e

    def _project_id(self, name: str) -> str:
        for p in self.list_projects():
            if p.name.casefold() == name.casefold():
                return p.id
        raise TodoistError(f"projet inconnu : {name}")

    @staticmethod
    def _to_task(t) -> Task:
        due = getattr(t.due, "string", None) if t.due else None
        return Task(id=t.id, content=t.content, project_id=t.project_id, priority=t.priority,
                    due=due, labels=list(t.labels or []), url=t.url,
                    is_completed=getattr(t, "completed_at", None) is not None)

    # --- TodoistPort ----------------------------------------------------
    def list_projects(self) -> list[Project]:
        return [Project(p.id, p.name) for p in self._call(lambda: _flatten(self._api.get_projects()))]

    def search_tasks(self, query=None, project=None, due_filter=None) -> list[Task]:
        parts = []
        if query:
            parts.append(f"search: {query}")
        if project:
            parts.append(f"#{project}")
        if due_filter:
            parts.append(f"({due_filter})")
        if parts:
            q = " & ".join(parts)
            tasks = self._call(lambda: _flatten(self._api.filter_tasks(query=q)))
        else:
            tasks = self._call(lambda: _flatten(self._api.get_tasks()))
        return [self._to_task(t) for t in tasks]

    def get_task(self, task_id: str) -> Task | None:
        return self._to_task(self._call(self._api.get_task, task_id))

    def create_task(self, action: Action) -> Task:
        kwargs = {k: v for k, v in {
            "description": action.description, "labels": action.labels,
            "priority": action.priority, "due_string": action.due_string,
        }.items() if v is not None}
        if action.project:
            kwargs["project_id"] = self._project_id(action.project)
        return self._to_task(self._call(self._api.add_task, action.content, **kwargs))

    def update_task(self, action: Action) -> Task:
        if action.project:
            self._call(self._api.move_task, action.task_id,
                       project_id=self._project_id(action.project))
        kwargs = {k: v for k, v in {
            "content": action.content, "description": action.description,
            "labels": action.labels, "priority": action.priority,
            "due_string": action.due_string,
        }.items() if v is not None}
        if kwargs:
            return self._to_task(self._call(self._api.update_task, action.task_id, **kwargs))
        return self.get_task(action.task_id)

    def complete_task(self, task_id: str) -> None:
        self._call(self._api.complete_task, task_id)

    def reopen_task(self, task_id: str) -> None:
        self._call(self._api.uncomplete_task, task_id)

    def delete_task(self, task_id: str) -> None:
        self._call(self._api.delete_task, task_id)
