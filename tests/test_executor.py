import pytest

from assistant.executor import Executor
from assistant.models import Action, ProposalStatus
from assistant.todoist_client import TodoistError
from tests.fakes import FakeTodoist


@pytest.fixture
def fake_todoist():
    return FakeTodoist()


@pytest.fixture
def executor(fake_todoist, store):
    return Executor(fake_todoist, store)


def test_executes_all(executor, store, fake_todoist):
    p = store.create([Action(kind="create", content="A"), Action(kind="create", content="B")], "x")
    done = executor.execute(p.id)
    assert done.status == ProposalStatus.DONE and len(fake_todoist.tasks) == 2
    assert done.results[0].url.startswith("https://") and done.results[0].task_id


def test_double_click_returns_none(executor, store, fake_todoist):
    p = store.create([Action(kind="create", content="A")], "x")
    executor.execute(p.id)
    assert executor.execute(p.id) is None
    assert len(fake_todoist.tasks) == 1


def test_partial_then_retry_remaining(executor, store, fake_todoist):
    p = store.create([Action(kind="create", content="A"), Action(kind="create", content="B")], "x")
    fake_todoist.fail_on["create_task"] = TodoistError("503")
    fake_todoist.fail_after["create_task"] = 1   # 1er appel OK, 2e en échec
    r = executor.execute(p.id)
    assert r.status == ProposalStatus.PARTIAL and [x.ok for x in r.results] == [True, False]
    assert "503" in r.results[1].error
    fake_todoist.fail_on.clear()
    r2 = executor.execute(p.id, retry=True)
    assert r2.status == ProposalStatus.DONE and len(fake_todoist.tasks) == 2  # A n'est pas recréée


def test_stops_at_first_error(executor, store, fake_todoist):
    p = store.create([Action(kind="complete", task_id="gone"), Action(kind="create", content="B")], "x")
    r = executor.execute(p.id)
    assert len(r.results) == 1 and fake_todoist.tasks == {}


def test_task_vanished(executor, store):
    p = store.create([Action(kind="complete", task_id="gone")], "x")
    r = executor.execute(p.id)
    assert "introuvable" in r.results[0].error


def test_retry_requires_partial(executor, store):
    p = store.create([Action(kind="create", content="A")], "x")
    assert executor.execute(p.id, retry=True) is None


def test_update_complete_reopen_delete(executor, store, fake_todoist):
    t = fake_todoist.add("A")
    p = store.create([Action(kind="update", task_id=t.id, priority=4),
                      Action(kind="complete", task_id=t.id),
                      Action(kind="reopen", task_id=t.id),
                      Action(kind="delete", task_id=t.id)], "x")
    assert executor.execute(p.id).status == ProposalStatus.DONE
    assert fake_todoist.tasks == {}


def test_unexpected_exception_marks_partial_not_stuck(executor, store, fake_todoist):
    fake_todoist.fail_on["create_task"] = TypeError("réponse malformée")
    p = store.create([Action(kind="create", content="A")], "x")
    r = executor.execute(p.id)
    assert r.status == ProposalStatus.PARTIAL and "malformée" in r.results[0].error
