"""Textes envoyés à Hermes."""
from __future__ import annotations

TASK_MARKER = "[Créer une tâche Todoist] "


def as_task(text: str) -> str:
    return TASK_MARKER + text


def edit_request(proposal_id: str, summary: str, correction: str) -> str:
    return (
        f"Je veux modifier la proposition {proposal_id} :\n{summary}\n\n"
        f"Correction demandée : {correction}\n\n"
        "Repropose la version corrigée complète (toutes les actions) via "
        f"todoist_propose_actions avec replaces_proposal_id={proposal_id}."
    )


PROMPT_SECTION = f"""\
## Tâches Todoist

- Tout message commençant par « {TASK_MARKER.strip()} » est une demande de tâche : \
appelle todoist_propose_actions pour la proposer.
- Todoist est le seul endroit pour les tâches, rappels et suivis, sauf demande explicite \
contraire de l'utilisateur.
- N'utilise jamais de liste interne, de fichier ni de cron pour une tâche.
- Avant de modifier, terminer ou supprimer une tâche, cherche-la avec todoist_search_tasks.
- Écris les échéances (due_string) en anglais, dans une forme que Todoist comprend \
(« tomorrow », « next friday at 9am »).
- N'essaie jamais d'écrire dans Todoist autrement que via todoist_propose_actions.
"""
