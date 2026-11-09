"""Évaluation de la compréhension avec le vrai Claude.

Usage : ANTHROPIC_API_KEY=... python evals/run_evals.py
Code de sortie 1 si moins de 90 % des cas sont corrects.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant.models import Answer, Clarification, ProposalDraft  # noqa: E402
from assistant.todoist_client import Project  # noqa: E402

PARIS = ZoneInfo("Europe/Paris")
THRESHOLD = 0.90
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
PRICES = {"in": 0.10, "out": 0.50, "cache_read": 0.01, "cache_write": 0.125}  # $/M tokens, Haiku 5.5


def resolve_due(due_string: str, now: datetime) -> date | None:
    """Traduction minimale d'une due_string Todoist (anglais) en date."""
    s = due_string.strip().lower()
    today = now.astimezone(PARIS).date()
    if m := re.match(r"^(\d{4}-\d{2}-\d{2})", s):
        return date.fromisoformat(m.group(1))
    s = re.sub(r"\s*(at\s+)?\d{1,2}(:\d{2})?\s*(am|pm)?$", "", s).strip()  # retire l'heure
    s = s.removeprefix("next ").removeprefix("this ").strip()
    if s in ("today", "tonight", "this evening"):
        return today
    if s == "tomorrow":
        return today + timedelta(days=1)
    for i, name in enumerate(WEEKDAYS):
        if s in (name, name[:3]):
            return today + timedelta(days=(i - today.weekday()) % 7)
    if m := re.match(r"^in (\d+) (day|week)s?$", s):
        n = int(m.group(1))
        return today + timedelta(days=n if m.group(2) == "day" else 7 * n)
    return None


def _match_action(exp: dict, a, todoist, now) -> str | None:
    """Renvoie None si l'action correspond, sinon la raison."""
    if a.kind.value != exp["kind"]:
        return f"type {a.kind.value} au lieu de {exp['kind']}"
    if "content_contains" in exp and exp["content_contains"].casefold() not in (a.content or "").casefold():
        return f"titre « {a.content} » sans « {exp['content_contains']} »"
    if "due_date" in exp:
        got = resolve_due(a.due_string, now) if a.due_string else None
        if str(got) != str(exp["due_date"]):
            return f"échéance « {a.due_string} » ({got}) au lieu de {exp['due_date']}"
    if "project" in exp and (a.project or "").casefold() != exp["project"].casefold():
        return f"projet {a.project} au lieu de {exp['project']}"
    if "priority" in exp and a.priority != exp["priority"]:
        return f"priorité {a.priority} au lieu de {exp['priority']}"
    if "task" in exp:
        task = todoist.tasks.get(a.task_id)
        if task is None or exp["task"].casefold() != task.content.casefold():
            return f"tâche visée {a.task_id} ({task.content if task else '?'}) au lieu de « {exp['task']} »"
    return None


def score_case(expect: dict, result, todoist, now: datetime) -> tuple[bool, str]:
    kinds = {"proposal": ProposalDraft, "answer": Answer, "clarification": Clarification}
    if not isinstance(result, kinds[expect["type"]]):
        return False, f"attendu {expect['type']}, obtenu {type(result).__name__}: {result}"
    if expect["type"] != "proposal":
        return True, "ok"
    wanted = expect["actions"]
    if len(result.actions) != len(wanted):
        return False, f"{len(result.actions)} action(s) au lieu de {len(wanted)}"
    remaining = list(result.actions)
    for exp in wanted:
        reasons = []
        for a in remaining:
            why = _match_action(exp, a, todoist, now)
            if why is None:
                remaining.remove(a)
                break
            reasons.append(why)
        else:
            return False, f"aucune action ne correspond à {exp} : " + " | ".join(reasons)
    return True, "ok"


def load_cases(path: Path = Path(__file__).with_name("phrases.yaml")) -> tuple[dict, list[dict]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data["seed"], data["cases"]


class _UsageRecorder:
    def __init__(self, client):
        self._client = client
        self.totals = {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0}
        self.messages = self

    def create(self, **kw):
        r = self._client.messages.create(**kw)
        u = r.usage
        self.totals["in"] += u.input_tokens
        self.totals["out"] += u.output_tokens
        self.totals["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        self.totals["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        return r

    def cost(self) -> float:
        return sum(self.totals[k] * PRICES[k] for k in PRICES) / 1_000_000


def main() -> int:
    import anthropic

    from assistant.agent.agent import Agent
    from assistant.conversation_store import ConversationStore
    from assistant.db import connect
    from tests.fakes import FakeTodoist

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY manquante : l'évaluation appelle le vrai Claude.")
        return 2
    seed, cases = load_cases()
    model = os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5")
    client = _UsageRecorder(anthropic.Anthropic(max_retries=2))
    passed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for i, case in enumerate(cases, 1):
            now = datetime.fromisoformat(case.get("now", seed["now"])).replace(tzinfo=PARIS)
            todoist = FakeTodoist(projects=[Project(f"p{j}", n) for j, n in enumerate(seed["projects"])])
            for t in seed["tasks"]:
                todoist.add(t["content"], project_id=f"p{seed['projects'].index(t.get('project', 'Inbox'))}")
            conv = ConversationStore(connect(Path(tmp) / f"c{i}.db"), lambda: now)
            agent = Agent(client, todoist, conv, model, lambda: now)
            try:
                result = agent.run(case["phrase"])
                ok, why = score_case(case["expect"], result, todoist, now)
            except Exception as e:  # noqa: BLE001
                ok, why = False, f"exception : {e!r}"
            passed += ok
            print(f"{'✅' if ok else '❌'} {i:02d}. {case['phrase']}" + ("" if ok else f"\n      → {why}"))
    rate = passed / len(cases)
    print(f"\nRéussite : {passed}/{len(cases)} = {rate:.0%} (seuil {THRESHOLD:.0%})")
    print(f"Tokens : {client.totals} — coût estimé : {client.cost():.4f} $")
    return 0 if rate >= THRESHOLD else 1


if __name__ == "__main__":
    sys.exit(main())
