from datetime import date

import pytest

from assistant.models import Action, Answer, Clarification, ProposalDraft
from evals.run_evals import load_cases, resolve_due, score_case
from tests.fakes import FakeTodoist


@pytest.mark.parametrize("s,expected", [
    ("thursday", date(2026, 11, 12)),
    ("thursday 18:00", date(2026, 11, 12)),
    ("tomorrow", date(2026, 11, 10)),
    ("today 18:00", date(2026, 11, 9)),
    ("tonight", date(2026, 11, 9)),
    ("monday", date(2026, 11, 9)),
    ("in 2 weeks", date(2026, 11, 23)),
    ("in 3 days", date(2026, 11, 12)),
    ("2026-12-01", date(2026, 12, 1)),
    ("whenever", None),
])
def test_resolve_due(now, s, expected):  # now = lundi 9/11/2026
    assert resolve_due(s, now) == expected


def test_score_proposal_match(now):
    ok, _ = score_case({"type": "proposal", "actions": [{"kind": "create", "content_contains": "pain",
                                                         "due_date": "2026-11-10"}]},
                       ProposalDraft([Action(kind="create", content="Acheter du pain", due_string="tomorrow")]),
                       FakeTodoist(), now)
    assert ok


def test_score_wrong_due(now):
    ok, why = score_case({"type": "proposal", "actions": [{"kind": "create", "due_date": "2026-11-12"}]},
                         ProposalDraft([Action(kind="create", content="x", due_string="tomorrow")]),
                         FakeTodoist(), now)
    assert not ok and "2026-11-12" in why


def test_score_existing_task_reference(now):
    t = FakeTodoist()
    task = t.add("Appeler maman")
    ok, _ = score_case({"type": "proposal", "actions": [{"kind": "complete", "task": "Appeler maman"}]},
                       ProposalDraft([Action(kind="complete", task_id=task.id)]), t, now)
    assert ok


def test_score_wrong_action_count(now):
    ok, why = score_case({"type": "proposal", "actions": [{"kind": "create"}, {"kind": "create"}]},
                         ProposalDraft([Action(kind="create", content="x")]), FakeTodoist(), now)
    assert not ok and "2" in why


def test_score_wrong_type(now):
    ok, why = score_case({"type": "clarification"}, Answer("x"), FakeTodoist(), now)
    assert not ok and "clarification" in why


def test_score_clarification_ok(now):
    assert score_case({"type": "clarification"}, Clarification("?"), FakeTodoist(), now)[0]


def test_dataset_has_at_least_30_cases():
    seed, cases = load_cases()
    assert len(cases) >= 30 and len(seed["tasks"]) >= 4
    types = [c["expect"]["type"] for c in cases]
    assert types.count("clarification") >= 2 and types.count("answer") >= 5
