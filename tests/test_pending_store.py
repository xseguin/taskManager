from datetime import timedelta

from assistant.models import Action, ActionResult, ProposalStatus


def _p(store):
    return store.create([Action(kind="create", content="x")], "x")


def test_create_and_get(store):
    p = _p(store)
    assert store.get(p.id).status == ProposalStatus.PENDING


def test_transition_is_exclusive(store):
    p = _p(store)
    assert store.transition(p.id, {ProposalStatus.PENDING}, ProposalStatus.EXECUTING) is True
    assert store.transition(p.id, {ProposalStatus.PENDING}, ProposalStatus.EXECUTING) is False
    assert store.get(p.id).status == ProposalStatus.EXECUTING


def test_expires_after_24h(store, clock):
    p = _p(store)
    clock.advance(timedelta(hours=24, seconds=1))
    assert store.get(p.id).status == ProposalStatus.EXPIRED
    assert store.transition(p.id, {ProposalStatus.PENDING}, ProposalStatus.EXECUTING) is False


def test_get_editing_and_message_id(store):
    p = _p(store)
    store.set_message_id(p.id, 99)
    store.transition(p.id, {ProposalStatus.PENDING}, ProposalStatus.EDITING)
    assert store.get_editing().message_id == 99


def test_unknown_id_returns_none(store):
    assert store.get("nope") is None


def test_save_results(store):
    p = _p(store)
    r = store.save_results(p.id, [ActionResult(index=0, ok=True, task_id="t1")], ProposalStatus.DONE)
    assert r.status == ProposalStatus.DONE and store.get(p.id).results[0].task_id == "t1"


def test_persists_across_connections(db_path, clock):
    from assistant.db import connect
    from assistant.pending_store import PendingStore
    p = PendingStore(connect(db_path), clock).create([Action(kind="create", content="x")], "x")
    assert PendingStore(connect(db_path), clock).get(p.id).id == p.id
