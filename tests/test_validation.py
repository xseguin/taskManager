from assistant.models import Action
from assistant.todoist_client import Project
from assistant.validation import validate_actions
from tests.fakes import FakeTodoist


def _todoist():
    return FakeTodoist(projects=[Project("p1", "Clients")])


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
