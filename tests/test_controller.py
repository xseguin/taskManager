import anthropic
import httpx
import pytest

from assistant.agent.agent import Agent
from assistant.controller import Controller
from assistant.conversation_store import ConversationStore
from assistant.executor import Executor
from assistant.models import ProposalStatus
from assistant.transcriber import TranscriptionError
from tests.fakes import FakeClaude, FakeTodoist, FakeTranscriber, response, text, tool_use

ME = 42
EXPIRED = "Cette proposition est expirée ou déjà traitée."


@pytest.fixture
def fake_todoist():
    return FakeTodoist()


@pytest.fixture
def fake_claude():
    return FakeClaude()


@pytest.fixture
def fake_transcriber():
    return FakeTranscriber(["devis paul jeudi"])


@pytest.fixture
def ctl(fake_todoist, fake_claude, fake_transcriber, store, conn, clock):
    agent = Agent(fake_claude, fake_todoist, ConversationStore(conn, clock), "m", clock)
    return Controller(ME, fake_transcriber, agent, store, Executor(fake_todoist, store))


def _propose(ctl, fake_claude, content="Devis Paul", message_id=10):
    fake_claude.push(response(tool_use("propose_create_task", {"content": content})))
    r = ctl.handle_text(ME, content)[-1]
    ctl._store.set_message_id(r.proposal_id, message_id)  # ce que fait l'adaptateur Telegram
    return r.proposal_id


def test_stranger_ignored(ctl, fake_claude):
    assert ctl.handle_text(7, "salut") == []
    assert ctl.handle_callback(7, "ok:abc", 1) == []
    assert fake_claude.calls == []


def test_voice_too_long_not_transcribed(ctl, fake_transcriber):
    r = ctl.handle_voice(ME, b"a", duration_s=301)
    assert "trop long" in r[0].text and fake_transcriber.calls == 0


def test_empty_transcription(ctl, fake_transcriber):
    fake_transcriber.results = ["  "]
    r = ctl.handle_voice(ME, b"a", duration_s=3)
    assert r[0].text == "Je n'ai pas compris, tu peux répéter ou l'écrire ?"


def test_voice_to_proposal_to_validate(ctl, fake_claude, fake_todoist):
    fake_claude.push(response(tool_use("propose_create_task",
                                       {"content": "Devis Paul", "due_string": "thursday"})))
    replies = ctl.handle_voice(ME, b"a", duration_s=8)
    assert replies[0].text == "🎙️ <i>« devis paul jeudi »</i>"
    assert replies[1].buttons[0].data.startswith("ok:")
    pid = replies[1].proposal_id
    done = ctl.handle_callback(ME, f"ok:{pid}", message_id=10)
    assert done[0].edit_message_id == 10 and "✅ Fait" in done[0].text
    assert fake_todoist.search_tasks(query="devis")


def test_double_click_no_duplicate(ctl, fake_claude, fake_todoist):
    pid = _propose(ctl, fake_claude)
    ctl.handle_callback(ME, f"ok:{pid}", 10)
    second = ctl.handle_callback(ME, f"ok:{pid}", 10)
    assert len(fake_todoist.tasks) == 1 and second[0].text == EXPIRED


def test_edit_flow_replaces_old(ctl, fake_claude, store):
    pid = _propose(ctl, fake_claude, message_id=10)
    r = ctl.handle_callback(ME, f"edit:{pid}", 10)
    assert r[0].text == "✏️ Que veux-tu changer ?"
    fake_claude.push(response(tool_use("propose_create_task",
                                       {"content": "Devis Paul", "due_string": "friday"})))
    replies = ctl.handle_text(ME, "plutôt vendredi")
    old = [x for x in replies if x.edit_message_id == 10][0]
    new = [x for x in replies if x.proposal_id][0]
    assert "Remplacé" in old.text and "friday" in new.text
    assert store.get(pid).status == ProposalStatus.REPLACED
    assert "Devis Paul" in fake_claude.calls[-1]["messages"][-1]["content"]


def test_clarification_reply(ctl, fake_claude):
    fake_claude.push(response(tool_use("ask_clarification", {"question": "Lequel ?",
                                                             "options": ["A", "B"]})))
    r = ctl.handle_text(ME, "le devis")
    assert r[0].text.startswith("❓ Lequel ?") and "• A" in r[0].text and not r[0].buttons


def test_answer_is_escaped(ctl, fake_claude):
    fake_claude.push(response(text("Tu as <rien> & tout")))
    assert ctl.handle_text(ME, "?")[0].text == "Tu as &lt;rien&gt; &amp; tout"


def test_transcription_failure_then_again(ctl, fake_transcriber, fake_claude):
    fake_transcriber.results = [TranscriptionError("down"), "bonjour"]
    r = ctl.handle_voice(ME, b"a", duration_s=3)
    assert r[0].text == "⚠️ La transcription a échoué."
    key = r[0].buttons[0].data
    assert key.startswith("again:")
    fake_claude.push(response(text("Salut !")))
    r2 = ctl.handle_callback(ME, key, 5)
    assert r2[0].text == "🎙️ <i>« bonjour »</i>"
    assert ctl.handle_callback(ME, key, 5)[0].text.startswith("Rien à réessayer")


def test_claude_down_offers_retry(ctl, fake_claude):
    fake_claude.push(anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")))
    r = ctl.handle_text(ME, "ajoute pain")
    assert r[0].text == "⚠️ Claude ne répond pas." and r[0].buttons[0].data.startswith("again:")
    fake_claude.push(response(tool_use("propose_create_task", {"content": "Pain"})))
    r2 = ctl.handle_callback(ME, r[0].buttons[0].data, 5)
    assert r2[0].proposal_id


def test_cancel(ctl, fake_claude, fake_todoist):
    pid = _propose(ctl, fake_claude)
    r = ctl.handle_callback(ME, f"cancel:{pid}", 10)
    assert r[0].edit_message_id == 10 and r[0].text.startswith("❌ Annulé")
    assert ctl.handle_callback(ME, f"ok:{pid}", 10)[0].text == EXPIRED
    assert fake_todoist.tasks == {}


def test_partial_offers_retry_remaining(ctl, fake_claude, fake_todoist):
    from assistant.todoist_client import TodoistError
    fake_claude.push(response(tool_use("propose_create_task", {"content": "A"}),
                              tool_use("propose_create_task", {"content": "B"})))
    pid = ctl.handle_text(ME, "A et B")[-1].proposal_id
    fake_todoist.fail_on["create_task"] = TodoistError("503")
    fake_todoist.fail_after["create_task"] = 1
    r = ctl.handle_callback(ME, f"ok:{pid}", 10)
    assert r[0].buttons[0].data == f"retry:{pid}"
    fake_todoist.fail_on.clear()
    r2 = ctl.handle_callback(ME, f"retry:{pid}", 10)
    assert "✅ Fait" in r2[0].text and len(fake_todoist.tasks) == 2


def test_unknown_callback(ctl):
    assert ctl.handle_callback(ME, "nimportequoi", 1)[0].text == EXPIRED


def test_cancel_while_editing(ctl, fake_claude, store):
    pid = _propose(ctl, fake_claude)
    ctl.handle_callback(ME, f"edit:{pid}", 10)
    r = ctl.handle_callback(ME, f"cancel:{pid}", 10)
    assert r[0].text.startswith("❌ Annulé") and store.get(pid).status == ProposalStatus.CANCELLED


def test_non_proposal_reply_while_editing_leaves_edit_mode(ctl, fake_claude, store):
    pid = _propose(ctl, fake_claude)
    ctl.handle_callback(ME, f"edit:{pid}", 10)
    fake_claude.push(response(text("Je ne gère que tes tâches.")))
    ctl.handle_text(ME, "quelle heure est-il ?")
    assert store.get(pid).status == ProposalStatus.PENDING
    fake_claude.push(response(tool_use("propose_create_task", {"content": "Pain"})))
    ctl.handle_text(ME, "ajoute pain")
    assert "Modification demandée" not in fake_claude.calls[-1]["messages"][-1]["content"]


def test_clarification_while_editing_keeps_edit_mode(ctl, fake_claude, store):
    pid = _propose(ctl, fake_claude)
    ctl.handle_callback(ME, f"edit:{pid}", 10)
    fake_claude.push(response(tool_use("ask_clarification", {"question": "Quel jour ?"})))
    ctl.handle_text(ME, "change la date")
    assert store.get(pid).status == ProposalStatus.EDITING


def test_unexpected_error_is_reported_with_retry(ctl, fake_claude):
    fake_claude.push(RuntimeError("bug"))
    r = ctl.handle_text(ME, "x")
    assert r[0].text == "⚠️ Erreur inattendue." and r[0].buttons[0].data.startswith("again:")
