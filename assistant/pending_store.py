"""Persistance des propositions et de leurs statuts."""
from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import uuid4

from assistant.models import Action, ActionResult, Proposal, ProposalStatus

EXPIRABLE = (ProposalStatus.PENDING, ProposalStatus.EDITING)


class PendingStore:
    def __init__(self, conn: sqlite3.Connection, clock: Callable[[], datetime],
                 ttl: timedelta = timedelta(hours=24)):
        self._conn = conn
        self._clock = clock
        self._ttl = ttl
        self._lock = threading.Lock()

    # --- lecture / écriture brutes -------------------------------------
    def _load(self, proposal_id: str) -> Proposal | None:
        row = self._conn.execute(
            "SELECT status, data FROM proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        if row is None:
            return None
        p = Proposal.model_validate_json(row[1])
        p.status = ProposalStatus(row[0])  # la colonne status fait foi
        return p

    def _write(self, p: Proposal) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO proposals (id, status, data) VALUES (?, ?, ?)",
            (p.id, p.status.value, p.model_dump_json()),
        )

    def _expire_if_needed(self, proposal_id: str) -> None:
        p = self._load(proposal_id)
        if p and p.status in EXPIRABLE and self._clock() > p.expires_at:
            p.status = ProposalStatus.EXPIRED
            self._write(p)

    # --- API publique ---------------------------------------------------
    def create(self, actions: list[Action], source_text: str) -> Proposal:
        now = self._clock()
        p = Proposal(id=uuid4().hex[:12], actions=actions, status=ProposalStatus.PENDING,
                     source_text=source_text, created_at=now, expires_at=now + self._ttl)
        with self._lock:
            self._write(p)
        return p

    def get(self, proposal_id: str) -> Proposal | None:
        with self._lock:
            self._expire_if_needed(proposal_id)
            return self._load(proposal_id)

    def transition(self, proposal_id: str, allowed_from: set[ProposalStatus],
                   to: ProposalStatus) -> bool:
        with self._lock:
            self._expire_if_needed(proposal_id)
            placeholders = ",".join("?" for _ in allowed_from)
            cur = self._conn.execute(
                f"UPDATE proposals SET status = ? WHERE id = ? AND status IN ({placeholders})",
                (to.value, proposal_id, *[s.value for s in allowed_from]),
            )
            if cur.rowcount != 1:
                return False
            p = self._load(proposal_id)
            self._write(p)  # resynchronise le JSON avec la colonne status
            return True

    def set_message_id(self, proposal_id: str, message_id: int) -> None:
        with self._lock:
            p = self._load(proposal_id)
            if p:
                p.message_id = message_id
                self._write(p)

    def save_results(self, proposal_id: str, results: list[ActionResult],
                     status: ProposalStatus) -> Proposal:
        with self._lock:
            p = self._load(proposal_id)
            p.results = results
            p.status = status
            self._write(p)
            return p

    def get_editing(self) -> Proposal | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM proposals WHERE status = ? ORDER BY rowid DESC LIMIT 1",
                (ProposalStatus.EDITING.value,),
            ).fetchone()
            if row is None:
                return None
            self._expire_if_needed(row[0])
            p = self._load(row[0])
            return p if p.status == ProposalStatus.EDITING else None
