from datetime import timedelta

import pytest

from assistant.models import Action, ActionResult, Proposal
from assistant.render import (proposal_buttons, render_proposal, render_results,
                              retry_remaining_button, split_message)


@pytest.fixture
def make_proposal(now):
    def make(actions, status="pending", results=()):
        return Proposal(id="abc", actions=actions, status=status, source_text="x",
                        created_at=now, expires_at=now + timedelta(hours=24), results=list(results))
    return make


def test_create_line_and_escaping(make_proposal):
    p = make_proposal([Action(kind="create", content="Lire <Dune> & co", due_string="jeudi",
                              project="Clients", priority=3)])
    s = render_proposal(p)
    assert "📝 <b>Créer</b>" in s and "Lire &lt;Dune&gt; &amp; co" in s
    assert "📅 jeudi" in s and "📁 Clients" in s and "🟠 P2" in s


def test_other_kinds_use_task_label(make_proposal):
    s = render_proposal(make_proposal([
        Action(kind="complete", task_id="1", task_label="Devis Paul"),
        Action(kind="update", task_id="2", task_label="Rapport", due_string="friday"),
        Action(kind="delete", task_id="3", task_label="Vieux"),
        Action(kind="reopen", task_id="4", task_label="Appel")]))
    assert "✅ <b>Terminer</b> : Devis Paul" in s and "✏️ <b>Modifier</b> : Rapport" in s
    assert "🗑️ <b>Supprimer</b> : Vieux" in s and "↩️ <b>Rouvrir</b> : Appel" in s
    assert "📅 friday" in s


def test_normal_priority_not_shown(make_proposal):
    assert "P4" not in render_proposal(make_proposal([Action(kind="create", content="x", priority=1)]))


def test_buttons():
    assert [b.data for b in proposal_buttons("abc")] == ["ta:p:ok:abc", "ta:p:edit:abc", "ta:p:cancel:abc"]
    assert retry_remaining_button("abc").data == "ta:p:retry:abc"


def test_done_results_with_link(make_proposal):
    p = make_proposal([Action(kind="create", content="A")], status="done",
                      results=[ActionResult(index=0, ok=True, task_id="1", url="https://t/1")])
    s = render_results(p)
    assert s.startswith("✅ Fait") and '<a href="https://t/1">' in s


def test_partial_results(make_proposal):
    p = make_proposal([Action(kind="create", content="A"), Action(kind="create", content="B")],
                      status="partial", results=[ActionResult(index=0, ok=True, url="https://t/1"),
                                                 ActionResult(index=1, ok=False, error="erreur Todoist : 503")])
    s = render_results(p)
    assert "✅" in s and "❌" in s and "503" in s


@pytest.mark.parametrize("status,head", [("cancelled", "❌ Annulé"), ("replaced", "↪️ Remplacé"),
                                         ("expired", "⌛ Expirée")])
def test_state_headers(make_proposal, status, head):
    s = render_results(make_proposal([Action(kind="create", content="A")], status=status))
    assert s.startswith(head) and "A" in s


def test_split_long_message():
    parts = split_message("ligne\n" * 1500)
    assert len(parts) >= 3 and all(len(x) <= 4096 for x in parts)
    assert "".join(parts).count("ligne") == 1500


def test_split_very_long_line():
    parts = split_message("x" * 9000)
    assert [len(p) for p in parts] == [4096, 4096, 808]
