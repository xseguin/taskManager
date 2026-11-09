from datetime import timedelta

import pytest
from pydantic import ValidationError

from assistant.models import Action, Answer, Proposal


def test_create_requires_content():
    with pytest.raises(ValidationError):
        Action(kind="create", content="  ")


def test_complete_requires_task_id():
    with pytest.raises(ValidationError):
        Action(kind="complete")


def test_update_requires_a_change():
    with pytest.raises(ValidationError):
        Action(kind="update", task_id="1")
    assert Action(kind="update", task_id="1", due_string="vendredi").due_string == "vendredi"


@pytest.mark.parametrize("p", [0, 5])
def test_priority_bounds(p):
    with pytest.raises(ValidationError):
        Action(kind="create", content="x", priority=p)


def test_proposal_roundtrip_json(now):
    p = Proposal(id="abc", actions=[Action(kind="create", content="x")], status="pending",
                 source_text="x", created_at=now, expires_at=now + timedelta(hours=24))
    assert Proposal.model_validate_json(p.model_dump_json()) == p


def test_agent_results_compare_by_value():
    assert Answer("x") == Answer("x")
