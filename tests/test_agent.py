from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from assistant.agent.agent import Agent
from assistant.agent.prompt import build_system_prompt
from assistant.conversation_store import ConversationStore
from assistant.models import Action, AgentFailure, Answer, Clarification, ProposalDraft
from assistant.todoist_client import Project
from tests.fakes import FakeClaude, FakeTodoist, response, text, tool_use


@pytest.fixture
def fake_todoist():
    return FakeTodoist(projects=[Project("p1", "Clients")])


@pytest.fixture
def conversation(conn, clock):
    return ConversationStore(conn, clock)


@pytest.fixture
def agent_factory(fake_todoist, conversation, clock):
    def make(claude):
        return Agent(claude, fake_todoist, conversation, "claude-haiku-5-5", clock)
    return make


def test_prompt_uses_paris_time_late_evening():
    now = datetime(2026, 11, 9, 23, 30, tzinfo=ZoneInfo("Europe/Paris"))
    s = " ".join(b["text"] for b in build_system_prompt(now, []))
    assert "lundi 9 novembre 2026" in s and "23:30" in s


def test_prompt_converts_utc_server_time_to_paris():
    now_utc = datetime(2026, 11, 9, 23, 30, tzinfo=ZoneInfo("UTC"))  # = mardi 00:30 à Paris
    s = " ".join(b["text"] for b in build_system_prompt(now_utc, [], ZoneInfo("Europe/Paris")))
    assert "mardi 10 novembre 2026" in s and "00:30" in s


def test_prompt_cache_on_fixed_block_and_lists_projects(now):
    blocks = build_system_prompt(now, [Project("p1", "Clients")])
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[-1] and "Clients" in blocks[-1]["text"]


def test_read_then_propose(agent_factory, fake_todoist):
    task = fake_todoist.add("Devis Paul")
    claude = FakeClaude([response(tool_use("search_tasks", {"query": "devis"})),
                         response(tool_use("propose_complete_task", {"task_id": task.id}))])
    res = agent_factory(claude).run("le devis Paul c'est fait")
    assert isinstance(res, ProposalDraft) and res.actions[0].task_label == "Devis Paul"
    assert len(claude.calls) == 2
    # le résultat de la recherche est bien renvoyé à Claude
    last_user = claude.calls[1]["messages"][-1]
    assert last_user["content"][0]["type"] == "tool_result" and "Devis Paul" in last_user["content"][0]["content"]


def test_text_only_is_answer(agent_factory):
    res = agent_factory(FakeClaude([response(text("Rien aujourd'hui."))])).run("quoi aujourd'hui ?")
    assert res == Answer("Rien aujourd'hui.")


def test_clarification(agent_factory):
    res = agent_factory(FakeClaude([response(tool_use("ask_clarification",
          {"question": "Lequel ?", "options": ["A", "B"]}))])).run("le devis")
    assert res == Clarification("Lequel ?", ["A", "B"])


def test_one_correction_then_failure(agent_factory):
    bad = response(tool_use("propose_complete_task", {"task_id": "zzz"}))
    claude = FakeClaude([bad, bad])
    res = agent_factory(claude).run("x")
    assert isinstance(res, AgentFailure) and len(claude.calls) == 2
    assert claude.calls[1]["messages"][-1]["content"][0]["is_error"] is True


def test_correction_succeeds(agent_factory, fake_todoist):
    task = fake_todoist.add("Devis Paul")
    claude = FakeClaude([response(tool_use("propose_complete_task", {"task_id": "zzz"})),
                         response(tool_use("propose_complete_task", {"task_id": task.id}))])
    assert isinstance(agent_factory(claude).run("x"), ProposalDraft)


def test_malformed_write_tool_counts_as_invalid(agent_factory):
    claude = FakeClaude([response(tool_use("propose_create_task", {})),
                         response(tool_use("propose_create_task", {"content": "Pain"}))])
    res = agent_factory(claude).run("x")
    assert res == ProposalDraft([Action(kind="create", content="Pain")])


def test_turn_limit(agent_factory):
    claude = FakeClaude([response(tool_use("list_projects", {})) for _ in range(7)])
    res = agent_factory(claude).run("x")
    assert res == AgentFailure("Demande trop complexe, reformule-la.") and len(claude.calls) == 6


def test_projects_cached_10_minutes(agent_factory, clock, fake_todoist):
    claude = FakeClaude([response(text("ok")) for _ in range(3)])
    agent = agent_factory(claude)
    agent.run("a"); clock.advance(timedelta(minutes=5)); agent.run("b")
    assert fake_todoist.calls["list_projects"] == 1
    clock.advance(timedelta(minutes=6)); agent.run("c")
    assert fake_todoist.calls["list_projects"] == 2


def test_history_is_sent_and_recorded(agent_factory, conversation):
    claude = FakeClaude([response(text("Noté.")), response(text("ok"))])
    agent = agent_factory(claude)
    agent.run("premier")
    agent.run("second")
    msgs = claude.calls[1]["messages"]
    assert [m["content"] for m in msgs[:2]] == ["premier", "Noté."]


def test_editing_includes_original_proposal(agent_factory, store):
    p = store.create([Action(kind="create", content="Devis Paul", due_string="thursday")], "x")
    claude = FakeClaude([response(text("ok"))])
    agent_factory(claude).run("plutôt vendredi", editing=p)
    sent = claude.calls[0]["messages"][-1]["content"]
    assert "Devis Paul" in sent and "plutôt vendredi" in sent


def test_empty_answer_gets_placeholder_and_history_stays_valid(agent_factory, conversation):
    claude = FakeClaude([response(), response(text("ok"))])
    agent = agent_factory(claude)
    assert agent.run("x") == Answer("…")
    agent.run("y")
    assert all(m["content"] for m in claude.calls[1]["messages"])


def test_todoist_outage_during_validation_does_not_escape(agent_factory, fake_todoist):
    from assistant.todoist_client import TodoistError
    fake_todoist.fail_on["get_task"] = TodoistError("HTTP 503")
    bad = response(tool_use("propose_complete_task", {"task_id": "t1"}))
    res = agent_factory(FakeClaude([bad, bad])).run("x")
    assert isinstance(res, AgentFailure) and "503" in res.message
