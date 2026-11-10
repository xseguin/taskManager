import pytest

from assistant.hermes.relay import Relay
from tests.fakes import FakeInjector


@pytest.fixture
def injector():
    return FakeInjector()


@pytest.fixture
def relay(injector):
    return Relay(allowed_user_ids=("42",), injector=injector)


def test_slash_t_becomes_task(relay):
    assert relay.rewrite("42", "42", "/t acheter du pain") == "[Créer une tâche Todoist] acheter du pain"

def test_slash_t_without_text_is_untouched(relay):
    assert relay.rewrite("42", "42", "/t") is None

def test_ok_releases_armed_text_once(relay):
    relay.arm_ok("42", "[Créer une tâche Todoist] pain")
    assert relay.rewrite("42", "42", "  OK ") == "[Créer une tâche Todoist] pain"
    assert relay.rewrite("42", "42", "ok") is None

def test_ok_without_pending_is_untouched(relay):
    assert relay.rewrite("42", "42", "ok") is None

def test_edit_armed_rewrites_next_message_only(relay):
    relay.arm_edit("42", "p1", "📝 Créer : pain")
    out = relay.rewrite("42", "42", "plutôt vendredi")
    assert "p1" in out and "plutôt vendredi" in out and "replaces_proposal_id" in out
    assert relay.rewrite("42", "42", "autre chose") is None

def test_rewrite_ignores_unauthorized_user(relay):
    relay.arm_ok("42", "secret")
    assert relay.rewrite("99", "42", "ok") is None
    assert relay.rewrite("99", "99", "/t x") is None
    assert relay.rewrite("42", "42", "ok") == "secret"

def test_deliver_injects_only_after_human_turn(relay, injector):
    assert relay.deliver("42", "pain") is False
    assert injector.calls == []
    assert relay.rewrite("42", "42", "ok") == "pain"
    relay.note_admission("42")
    assert relay.deliver("42", "lait") is True
    assert injector.calls == [("42", "lait")]

def test_reset_forgets_human_turn(relay, injector):
    relay.note_admission("42"); relay.note_reset("42")
    assert relay.deliver("42", "pain") is False

def test_refused_injection_arms_fallback(relay, injector):
    relay.note_admission("42"); injector.accept = False
    assert relay.deliver("42", "pain") is False
    assert relay.rewrite("42", "42", "ok") == "pain"
