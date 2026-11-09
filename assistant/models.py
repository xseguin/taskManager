"""Types de données partagés : actions, propositions, résultats de l'agent."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, model_validator


class ActionKind(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    COMPLETE = "complete"
    REOPEN = "reopen"
    DELETE = "delete"


UPDATABLE_FIELDS = ("content", "due_string", "project", "priority", "labels", "description")


class Action(BaseModel):
    kind: ActionKind
    task_id: str | None = None
    content: str | None = None
    due_string: str | None = None
    project: str | None = None
    priority: int | None = None  # échelle API Todoist : 1 = normale … 4 = urgente
    labels: list[str] | None = None
    description: str | None = None
    task_label: str | None = None  # titre actuel, rempli par la validation

    @model_validator(mode="after")
    def _check(self) -> "Action":
        if self.kind == ActionKind.CREATE:
            if not (self.content or "").strip():
                raise ValueError("une création exige un titre (content)")
        elif not (self.task_id or "").strip():
            raise ValueError(f"l'action {self.kind} exige un task_id")
        if self.kind == ActionKind.UPDATE and all(
            getattr(self, f) is None for f in UPDATABLE_FIELDS
        ):
            raise ValueError("une modification exige au moins un champ modifié")
        if self.priority is not None and not 1 <= self.priority <= 4:
            raise ValueError("priority doit être entre 1 et 4")
        return self


class ProposalStatus(StrEnum):
    PENDING = "pending"
    EXECUTING = "executing"
    DONE = "done"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    EDITING = "editing"
    REPLACED = "replaced"
    EXPIRED = "expired"


class ActionResult(BaseModel):
    index: int
    ok: bool
    task_id: str | None = None
    url: str | None = None
    error: str | None = None


class Proposal(BaseModel):
    id: str
    actions: list[Action]
    status: ProposalStatus
    source_text: str
    created_at: datetime
    expires_at: datetime
    message_id: int | None = None
    results: list[ActionResult] = []


@dataclass(frozen=True)
class ProposalDraft:
    actions: list[Action]


@dataclass(frozen=True)
class Answer:
    text: str


@dataclass(frozen=True)
class Clarification:
    question: str
    options: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AgentFailure:
    message: str


AgentResult = ProposalDraft | Answer | Clarification | AgentFailure
