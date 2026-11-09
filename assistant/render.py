"""Textes HTML et boutons affichés dans Telegram."""
from __future__ import annotations

from dataclasses import dataclass
from html import escape

from assistant.models import Action, ActionKind, Proposal, ProposalStatus

TELEGRAM_LIMIT = 4096


@dataclass(frozen=True)
class Button:
    text: str
    data: str


VERB = {
    ActionKind.CREATE: "📝 <b>Créer</b>",
    ActionKind.UPDATE: "✏️ <b>Modifier</b>",
    ActionKind.COMPLETE: "✅ <b>Terminer</b>",
    ActionKind.REOPEN: "↩️ <b>Rouvrir</b>",
    ActionKind.DELETE: "🗑️ <b>Supprimer</b>",
}
PRIORITY = {4: "🔴 P1", 3: "🟠 P2", 2: "🔵 P3"}
HEADER = {
    ProposalStatus.DONE: "✅ Fait",
    ProposalStatus.PARTIAL: "⚠️ Partiellement fait",
    ProposalStatus.CANCELLED: "❌ Annulé",
    ProposalStatus.REPLACED: "↪️ Remplacé",
    ProposalStatus.EXPIRED: "⌛ Expirée",
}


def _action_lines(a: Action) -> list[str]:
    title = a.content if a.kind == ActionKind.CREATE else (a.task_label or a.task_id)
    lines = [f"{VERB[a.kind]} : {escape(title or '')}"]
    if a.kind == ActionKind.UPDATE and a.content:
        lines.append(f"✏️ nouveau titre : {escape(a.content)}")
    details = []
    if a.due_string:
        details.append(f"📅 {escape(a.due_string)}")
    if a.project:
        details.append(f"📁 {escape(a.project)}")
    if a.priority in PRIORITY:
        details.append(PRIORITY[a.priority])
    if a.labels:
        details.append("🏷️ " + ", ".join(escape(l) for l in a.labels))
    if details:
        lines.append(" · ".join(details))
    if a.description:
        lines.append(f"<i>{escape(a.description)}</i>")
    return lines


def render_proposal(p: Proposal) -> str:
    return "\n\n".join("\n".join(_action_lines(a)) for a in p.actions)


def proposal_buttons(proposal_id: str) -> list[Button]:
    return [Button("✅ Valider", f"ok:{proposal_id}"), Button("✏️ Modifier", f"edit:{proposal_id}"),
            Button("❌ Annuler", f"cancel:{proposal_id}")]


def retry_remaining_button(proposal_id: str) -> Button:
    return Button("🔁 Réessayer ce qui reste", f"retry:{proposal_id}")


def render_results(p: Proposal) -> str:
    header = HEADER.get(p.status, "")
    results = {r.index: r for r in p.results}
    blocks = []
    for i, a in enumerate(p.actions):
        lines = _action_lines(a)
        r = results.get(i)
        if p.status in (ProposalStatus.DONE, ProposalStatus.PARTIAL):
            if r is None:
                lines[0] = "⏸️ " + lines[0]
            elif r.ok:
                if r.url:
                    lines.append(f'<a href="{escape(r.url)}">Ouvrir dans Todoist</a>')
                if p.status == ProposalStatus.PARTIAL:
                    lines[0] = "✅ " + lines[0]
            else:
                lines[0] = "❌ " + lines[0]
                lines.append(f"⚠️ {escape(r.error or 'erreur')}")
        blocks.append("\n".join(lines))
    return header + "\n\n" + "\n\n".join(blocks)


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    parts: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            parts.append(current)
            current = ""
        current += line
    if current:
        parts.append(current)
    return parts or [""]
