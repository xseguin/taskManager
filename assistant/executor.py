"""Exécution d'une proposition validée."""
from __future__ import annotations

import logging

from assistant.models import ActionKind, ActionResult, Proposal, ProposalStatus
from assistant.pending_store import PendingStore
from assistant.todoist_client import TaskNotFound, TodoistError, TodoistPort

log = logging.getLogger(__name__)


class Executor:
    def __init__(self, todoist: TodoistPort, store: PendingStore):
        self._todoist = todoist
        self._store = store

    def execute(self, proposal_id: str, retry: bool = False) -> Proposal | None:
        start = ProposalStatus.PARTIAL if retry else ProposalStatus.PENDING
        if not self._store.transition(proposal_id, {start}, ProposalStatus.EXECUTING):
            return None
        p = self._store.get(proposal_id)
        done = {r.index: r for r in p.results if r.ok}
        results: list[ActionResult] = []
        for i, a in enumerate(p.actions):
            if i in done:
                results.append(done[i])
                continue
            try:
                task_id, url = a.task_id, None
                match a.kind:
                    case ActionKind.CREATE:
                        t = self._todoist.create_task(a)
                        task_id, url = t.id, t.url
                    case ActionKind.UPDATE:
                        t = self._todoist.update_task(a)
                        url = t.url
                    case ActionKind.COMPLETE:
                        self._todoist.complete_task(a.task_id)
                    case ActionKind.REOPEN:
                        self._todoist.reopen_task(a.task_id)
                    case ActionKind.DELETE:
                        self._todoist.delete_task(a.task_id)
                results.append(ActionResult(index=i, ok=True, task_id=task_id, url=url))
            except TaskNotFound:
                results.append(ActionResult(index=i, ok=False, task_id=a.task_id,
                               error="tâche introuvable (modifiée ou supprimée entre-temps)"))
                break
            except TodoistError as e:
                log.warning("échec Todoist sur l'action %d de %s : %s", i, proposal_id, e)
                results.append(ActionResult(index=i, ok=False, task_id=a.task_id,
                               error=f"erreur Todoist : {e}"))
                break
        ok = len(results) == len(p.actions) and all(r.ok for r in results)
        return self._store.save_results(
            proposal_id, results, ProposalStatus.DONE if ok else ProposalStatus.PARTIAL)
