"""Faux services externes pour les tests."""
from __future__ import annotations

from collections import Counter
from itertools import count

from assistant.models import Action
from assistant.todoist_client import Project, Task, TaskNotFound, TodoistError


class FakeTodoist:
    def __init__(self, projects: list[Project] | None = None):
        self.projects = projects or [Project("inbox", "Inbox")]
        self.tasks: dict[str, Task] = {}
        self.fail_on: dict[str, Exception] = {}
        self.fail_after: dict[str, int] = {}
        self.calls: Counter[str] = Counter()
        self._ids = count(1)

    def _tick(self, method: str) -> None:
        self.calls[method] += 1
        if method in self.fail_on and self.calls[method] > self.fail_after.get(method, 0):
            raise self.fail_on[method]

    def _require(self, task_id: str) -> Task:
        if task_id not in self.tasks:
            raise TaskNotFound(task_id)
        return self.tasks[task_id]

    def _project_id(self, name: str | None) -> str:
        if name is None:
            return self.projects[0].id
        for p in self.projects:
            if p.name.casefold() == name.casefold():
                return p.id
        raise TodoistError(f"projet inconnu : {name}")

    def add(self, content: str, **kw) -> Task:
        """Raccourci de test : crée une tâche sans passer par les compteurs."""
        tid = f"t{next(self._ids)}"
        t = Task(id=tid, content=content, project_id=kw.get("project_id", self.projects[0].id),
                 priority=kw.get("priority", 1), due=kw.get("due"), labels=kw.get("labels", []),
                 url=f"https://todoist.test/{tid}")
        self.tasks[tid] = t
        return t

    def list_projects(self) -> list[Project]:
        self._tick("list_projects")
        return list(self.projects)

    def search_tasks(self, query=None, project=None, due_filter=None) -> list[Task]:
        self._tick("search_tasks")
        res = [t for t in self.tasks.values() if not t.is_completed]
        if query:
            res = [t for t in res if query.casefold() in t.content.casefold()]
        if project:
            pid = self._project_id(project)
            res = [t for t in res if t.project_id == pid]
        return res

    def get_task(self, task_id: str) -> Task | None:
        self._tick("get_task")
        return self._require(task_id)

    def create_task(self, action: Action) -> Task:
        self._tick("create_task")
        return self.add(action.content, project_id=self._project_id(action.project),
                        priority=action.priority or 1, due=action.due_string,
                        labels=action.labels or [])

    def update_task(self, action: Action) -> Task:
        self._tick("update_task")
        t = self._require(action.task_id)
        for f in ("content", "priority", "labels"):
            if getattr(action, f) is not None:
                setattr(t, f, getattr(action, f))
        if action.due_string is not None:
            t.due = action.due_string
        if action.project is not None:
            t.project_id = self._project_id(action.project)
        return t

    def complete_task(self, task_id: str) -> None:
        self._tick("complete_task")
        self._require(task_id).is_completed = True

    def reopen_task(self, task_id: str) -> None:
        self._tick("reopen_task")
        self._require(task_id).is_completed = False

    def delete_task(self, task_id: str) -> None:
        self._tick("delete_task")
        self._require(task_id)
        del self.tasks[task_id]



class FakeInjector:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.accept = True

    def inject(self, chat_id: str, text: str) -> bool:
        self.calls.append((chat_id, text))
        return self.accept


class FakeMessenger:
    def __init__(self):
        self.sent: list[tuple[str, str, list | None]] = []
        self.edits: list[tuple[str, int, str, list | None]] = []
        self._ids = count(1)

    async def send(self, chat_id, text, buttons=None) -> int:
        self.sent.append((chat_id, text, buttons))
        return next(self._ids)

    async def edit(self, chat_id, message_id, text, buttons=None) -> None:
        self.edits.append((chat_id, message_id, text, buttons))
