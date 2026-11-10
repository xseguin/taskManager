import json

import pytest

from assistant.executor import Executor
from assistant.hermes.relay import Relay
from assistant.hermes.todoist_tools import ProposalFlow, TOOL_SCHEMAS, run_read_tool
from assistant.models import ProposalStatus
from assistant.todoist_client import TodoistError
from tests.fakes import FakeInjector, FakeMessenger, FakeTodoist


@pytest.fixture
def todoist():
    return FakeTodoist()


@pytest.fixture
def messenger():
    return FakeMessenger()


@pytest.fixture
def relay():
    return Relay(("42",), FakeInjector())


@pytest.fixture
def flow(store, todoist, messenger, relay):
    return ProposalFlow(store, Executor(todoist, store), todoist, messenger, relay, "42")


EXPIRED = "Cette proposition est expirée ou déjà traitée."


async def test_propose_sends_card_and_writes_nothing(flow, todoist, messenger):
    out = json.loads(await flow.propose({"actions": [{"kind": "create", "content": "Pain"}]}))
    assert out["status"] == "pending_user_validation"
    assert todoist.calls["create_task"] == 0
    chat, text, buttons = messenger.sent[-1]
    assert chat == "42" and "Pain" in text
    assert [b.data for b in buttons] == [f"ta:p:{v}:{out['proposal_id']}" for v in ("ok", "edit", "cancel")]


async def test_propose_invalid_returns_error_without_card(flow, messenger):
    out = json.loads(await flow.propose({"actions": [{"kind": "complete"}]}))
    assert "error" in out and messenger.sent == []


async def test_propose_unknown_task_returns_validation_error(flow):
    out = json.loads(await flow.propose({"actions": [{"kind": "delete", "task_id": "nope"}]}))
    assert "introuvable" in out["error"]


async def test_propose_empty_actions(flow, messenger):
    out = json.loads(await flow.propose({"actions": []}))
    assert "error" in out and messenger.sent == []


async def test_multi_actions_single_card(flow, todoist, messenger):
    t = todoist.add("Lait")
    await flow.propose({"actions": [{"kind": "create", "content": "Pain"},
                                    {"kind": "complete", "task_id": t.id}]})
    assert len(messenger.sent) == 1


async def test_validate_executes_once(flow, todoist, messenger):
    pid = json.loads(await flow.propose({"actions": [{"kind": "create", "content": "Pain"}]}))["proposal_id"]
    await flow.on_button("42", 1, "ok", pid)
    await flow.on_button("42", 1, "ok", pid)
    assert todoist.calls["create_task"] == 1
    assert messenger.edits[0][2].startswith("✅ Fait")
    assert messenger.sent[-1][1] == EXPIRED


async def test_partial_offers_retry(flow, todoist, messenger):
    todoist.fail_on["create_task"] = TodoistError("boom")
    todoist.fail_after["create_task"] = 1
    pid = json.loads(await flow.propose({"actions": [{"kind": "create", "content": "A"},
                                                     {"kind": "create", "content": "B"}]}))["proposal_id"]
    await flow.on_button("42", 1, "ok", pid)
    assert messenger.edits[-1][3][0].data == f"ta:p:retry:{pid}"
    todoist.fail_on.clear()
    await flow.on_button("42", 1, "retry", pid)
    assert todoist.calls["create_task"] == 3
    assert messenger.edits[-1][2].startswith("✅ Fait")


async def test_cancel(flow, todoist, messenger):
    pid = json.loads(await flow.propose({"actions": [{"kind": "create", "content": "Pain"}]}))["proposal_id"]
    await flow.on_button("42", 1, "cancel", pid)
    assert todoist.calls["create_task"] == 0 and messenger.edits[-1][2].startswith("❌ Annulé")


async def test_edit_arms_relay_and_replacement_closes_old_card(flow, relay, messenger, store):
    old = json.loads(await flow.propose({"actions": [{"kind": "create", "content": "Pain"}]}))["proposal_id"]
    await flow.on_button("42", 1, "edit", old)
    assert messenger.sent[-1][1] == "✏️ Que veux-tu changer ?"
    assert old in relay.rewrite("42", "42", "plutôt vendredi")
    await flow.propose({"actions": [{"kind": "create", "content": "Pain", "due_string": "friday"}],
                        "replaces_proposal_id": old})
    assert store.get(old).status == ProposalStatus.REPLACED
    assert any(e[1] == 1 and e[2].startswith("↪️ Remplacé") for e in messenger.edits)


async def test_click_on_replaced_proposal_refused(flow, todoist, messenger):
    old = json.loads(await flow.propose({"actions": [{"kind": "create", "content": "Pain"}]}))["proposal_id"]
    await flow.propose({"actions": [{"kind": "create", "content": "Pain 2"}], "replaces_proposal_id": old})
    await flow.on_button("42", 1, "ok", old)
    assert todoist.calls["create_task"] == 0
    assert messenger.sent[-1][1] == EXPIRED


async def test_unknown_proposal_refused(flow, messenger):
    await flow.on_button("42", 1, "ok", "zzz")
    assert messenger.sent[-1][1] == EXPIRED


async def test_expired_click_rewrites_card(flow, clock, messenger):
    from datetime import timedelta
    pid = json.loads(await flow.propose({"actions": [{"kind": "create", "content": "Pain"}]}))["proposal_id"]
    clock.advance(timedelta(hours=25))
    await flow.on_button("42", 1, "ok", pid)
    assert messenger.edits[-1][2].startswith("⌛ Expirée")
    assert messenger.sent[-1][1] == EXPIRED


def test_read_tools(todoist):
    todoist.add("Pain")
    assert "Pain" in run_read_tool("todoist_search_tasks", {"query": "pain"}, todoist)
    assert "introuvable" in run_read_tool("todoist_get_task", {"task_id": "x"}, todoist)


def test_schemas():
    assert set(TOOL_SCHEMAS) == {"todoist_list_projects", "todoist_search_tasks",
                                 "todoist_get_task", "todoist_propose_actions"}
    for name, s in TOOL_SCHEMAS.items():
        assert s["name"] == name and s["description"] and s["parameters"]["type"] == "object"
    assert TOOL_SCHEMAS["todoist_propose_actions"]["parameters"]["required"] == ["actions"]
