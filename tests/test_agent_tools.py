import pytest

from assistant.agent.tools import (READ_TOOLS, TOOLS, WRITE_TOOLS, action_from_tool,
                                   run_read_tool)
from assistant.agent.validation import validate_actions
from assistant.models import Action, ActionKind
from assistant.todoist_client import Project
from tests.fakes import FakeTodoist


def _todoist():
    return FakeTodoist(projects=[Project("p1", "Clients")])


def test_action_from_tool_create():
    a = action_from_tool("propose_create_task", {"content": "Devis", "priority": 3})
    assert a.kind == ActionKind.CREATE and a.priority == 3


def test_action_from_tool_invalid_raises():
    with pytest.raises(ValueError):
        action_from_tool("propose_complete_task", {})


def test_validate_unknown_task_and_project():
    errs = validate_actions([Action(kind="complete", task_id="zzz"),
                             Action(kind="create", content="x", project="Inconnu")], _todoist())
    assert len(errs) == 2 and "zzz" in errs[0] and "Inconnu" in errs[1]


def test_validate_fills_task_label():
    t = _todoist()
    task = t.add("Devis Paul")
    a = Action(kind="complete", task_id=task.id)
    assert validate_actions([a], t) == [] and a.task_label == "Devis Paul"


def test_validate_rejects_completing_done_task_and_reopening_open_task():
    t = _todoist()
    done, open_ = t.add("A"), t.add("B")
    done.is_completed = True
    errs = validate_actions([Action(kind="complete", task_id=done.id),
                             Action(kind="reopen", task_id=open_.id)], t)
    assert len(errs) == 2


def test_tools_last_has_cache_control():
    assert TOOLS[-1]["cache_control"] == {"type": "ephemeral"}
    names = {t["name"] for t in TOOLS}
    assert READ_TOOLS | WRITE_TOOLS | {"ask_clarification"} == names


def test_run_read_tool_search():
    t = _todoist()
    t.add("Devis Paul")
    out = run_read_tool("search_tasks", {"query": "devis"}, t)
    assert "Devis Paul" in out


def test_run_read_tool_get_missing_task():
    assert "introuvable" in run_read_tool("get_task", {"task_id": "zz"}, _todoist())
