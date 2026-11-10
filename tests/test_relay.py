from datetime import timedelta

import pytest

from assistant.hermes.relay import Relay


@pytest.fixture
def relay(clock):
    return Relay(allowed_user_ids=("42",), clock=clock)


def test_slash_t_becomes_task(relay):
    assert relay.rewrite("42", "42", "/t acheter du pain") == "[Créer une tâche Todoist] acheter du pain"

def test_slash_t_without_text_is_untouched(relay):
    assert relay.rewrite("42", "42", "/t") is None

def test_ok_is_no_longer_special(relay):
    assert relay.rewrite("42", "42", "ok") is None

def test_edit_armed_rewrites_next_message_only(relay):
    relay.arm_edit("42", "p1", "📝 Créer : pain")
    out = relay.rewrite("42", "42", "plutôt vendredi")
    assert "p1" in out and "plutôt vendredi" in out and "replaces_proposal_id" in out
    assert relay.rewrite("42", "42", "autre chose") is None

def test_rewrite_ignores_unauthorized_user(relay):
    relay.arm_edit("42", "p1", "s")
    assert relay.rewrite("99", "42", "plutôt lundi") is None
    assert relay.rewrite("99", "99", "/t x") is None
    assert "p1" in relay.rewrite("42", "42", "plutôt lundi")

def test_edit_expires_after_ten_minutes(relay, clock):
    relay.arm_edit("42", "p1", "s")
    clock.advance(timedelta(minutes=10, seconds=1))
    assert relay.rewrite("42", "42", "plutôt vendredi") is None

def test_edit_still_valid_before_ten_minutes(relay, clock):
    relay.arm_edit("42", "p1", "s")
    clock.advance(timedelta(minutes=9))
    assert "p1" in relay.rewrite("42", "42", "plutôt vendredi")

def test_drop_edit_only_for_that_proposal(relay):
    relay.arm_edit("42", "p1", "s")
    relay.drop_edit("p2")
    relay.drop_edit("p1")
    assert relay.rewrite("42", "42", "plutôt vendredi") is None

def test_drop_edit_keeps_other_proposal(relay):
    relay.arm_edit("42", "p1", "s")
    relay.drop_edit("p2")
    assert "p1" in relay.rewrite("42", "42", "plutôt vendredi")

def test_synthetic_voice_text_is_never_read_as_slash_t(relay):
    assert relay.rewrite("42", "42", "/t acheter du pain", synthetic=True) is None

def test_synthetic_voice_text_becomes_the_correction(relay):
    relay.arm_edit("42", "p1", "📝 Créer : pain")
    out = relay.rewrite("42", "42", "plutôt samedi", synthetic=True)
    assert "p1" in out and "Correction demandée : plutôt samedi" in out

def test_synthetic_task_marker_is_stripped_from_correction(relay):
    relay.arm_edit("42", "p1", "s")
    out = relay.rewrite("42", "42", "[Créer une tâche Todoist] plutôt samedi", synthetic=True)
    assert "Correction demandée : plutôt samedi" in out
