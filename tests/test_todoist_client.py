from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from assistant.models import Action
from assistant.todoist_client import (Project, Task, TaskNotFound, TodoistClient, TodoistError,
                                      format_tasks_for_llm)
from tests.fakes import FakeTodoist


def _sdk_task(id="t1", content="x", project_id="p1"):
    return SimpleNamespace(id=id, content=content, project_id=project_id, priority=1, due=None,
                           labels=[], url=f"https://app.todoist.com/app/task/{id}", completed_at=None)


def _http_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://api.todoist.com/x")
    return httpx.HTTPStatusError("err", request=req, response=httpx.Response(status, request=req))


@pytest.fixture
def client():
    c = TodoistClient.__new__(TodoistClient)
    c._api = MagicMock()
    c._api.get_projects.return_value = iter([[SimpleNamespace(id="p1", name="Clients")]])
    return c


def test_format_caps_at_30():
    tasks = [Task(str(i), f"t{i}", "p", 1, None, [], "u") for i in range(45)]
    out = format_tasks_for_llm(tasks)
    assert out.count("\n") == 30 and out.endswith("(15 autres non affichés)")


def test_format_empty():
    assert format_tasks_for_llm([]) == "Aucune tâche trouvée."


def test_client_maps_project_name_case_insensitive(client):
    client._api.add_task.return_value = _sdk_task()
    client.create_task(Action(kind="create", content="x", project="clients"))
    assert client._api.add_task.call_args.kwargs["project_id"] == "p1"


def test_client_404_raises_task_not_found(client):
    client._api.get_task.side_effect = _http_error(404)
    with pytest.raises(TaskNotFound):
        client.get_task("zzz")


def test_client_other_http_error_raises_todoist_error(client):
    client._api.complete_task.side_effect = _http_error(503)
    with pytest.raises(TodoistError) as e:
        client.complete_task("t1")
    assert not isinstance(e.value, TaskNotFound)


def test_client_search_builds_filter(client):
    client._api.filter_tasks.return_value = iter([[_sdk_task()]])
    client.search_tasks(query="devis", project="Clients", due_filter="today")
    assert client._api.filter_tasks.call_args.kwargs["query"] == "search: devis & #Clients & (today)"


def test_client_update_with_project_moves_task(client):
    client._api.update_task.return_value = _sdk_task()
    client._api.get_task.return_value = _sdk_task()
    client.update_task(Action(kind="update", task_id="t1", project="Clients", priority=4))
    assert client._api.move_task.call_args.kwargs["project_id"] == "p1"
    assert client._api.update_task.call_args.kwargs == {"priority": 4}


def test_fake_todoist_contract():
    t = FakeTodoist(projects=[Project("p1", "Clients")])
    task = t.create_task(Action(kind="create", content="Devis Paul", project="Clients"))
    assert t.search_tasks(query="devis")[0].id == task.id
    t.complete_task(task.id)
    assert t.get_task(task.id).is_completed
    with pytest.raises(TaskNotFound):
        t.complete_task("gone")
