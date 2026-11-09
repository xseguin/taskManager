# Assistant vocal de tâches — Sous-projet 1 : capture et mise à jour par la voix

Date : 2026-11-09
Statut : en relecture

## 1. Contexte et objectif

Outil personnel de productivité destiné à résoudre quatre frictions :

- **A.** des idées/tâches perdues faute de temps pour les noter ;
- **B.** pas de vision claire de ce qui est en cours ou en retard ;
- **C.** des mails qui génèrent des actions oubliées ;
- **D.** pas de temps trouvé dans l'agenda pour faire les tâches.

Le projet est découpé en sous-projets, chacun avec sa propre spec :

1. **Socle + capture vocale (A)** — *ce document*
2. Points d'étape vocaux : brief du matin, revue du soir / de la semaine (B)
3. Mail → tâche depuis Gmail (C)
4. Planification dans Google Calendar (D)

### Ce que l'utilisateur a demandé
- Interface : **Telegram** (messages vocaux et texte).
- Stockage des tâches : **Todoist**.
- Langage : Python ou JS ; hébergement sur **son VPS**.
- Mode d'action : **Claude propose, l'utilisateur valide** avant toute écriture.

### Hypothèses (validées implicitement, à corriger si besoin)
- Un seul utilisateur ; tout autre compte Telegram est ignoré.
- Langue : français. Fuseau horaire : Europe/Paris.
- Gmail et Google Calendar sont hors périmètre de ce sous-projet.

### Critère de succès
Un message vocal d'environ 10 secondes produit une proposition correcte, transformée en tâche Todoist en **un seul clic** de validation, en moins de ~10 secondes de bout en bout.

## 2. Périmètre

### Inclus
- Créer, modifier (titre, échéance, projet, priorité, étiquettes, description), terminer, rouvrir et supprimer des tâches Todoist.
- Plusieurs actions dans un même message.
- Questions en lecture seule : « qu'est-ce que j'ai aujourd'hui / cette semaine / en retard ? », « où en est X ? ».
- Contexte conversationnel court (« décale-la à lundi »).
- Clarification quand la demande est ambiguë.

### Exclus (sous-projets suivants ou plus tard)
- Gmail, Google Calendar, briefs programmés.
- Réponses vocales (le bot répond en texte).
- Création/suppression de projets Todoist.
- Interface web.

## 3. Architecture

Un seul service **Python** sur le VPS, en **long polling** Telegram (pas de domaine ni HTTPS requis), lancé par **systemd**.

Bibliothèques : `python-telegram-bot`, `anthropic`, `todoist-api-python`, `groq`, `pydantic`, `sqlite3` (stdlib), `pytest`.

### Composants

| Module | Rôle | Dépend de |
|---|---|---|
| `config` | Charge le `.env` (clés API, ID Telegram autorisé, modèle, fuseau) | — |
| `telegram_bot` | Reçoit messages et clics, filtre l'utilisateur, télécharge les vocaux, affiche transcription / propositions / résultats | `transcriber`, `agent`, `pending_store`, `executor` |
| `transcriber` | `transcribe(audio: bytes) -> str`. Implémentation par défaut : Groq `whisper-large-v3-turbo`, langue `fr` | API Groq |
| `agent` | Envoie la demande à Claude avec les outils ; exécute les outils de lecture ; retourne un `AgentResult` | `todoist_client`, `conversation_store`, API Anthropic |
| `todoist_client` | Fine couche sur l'API Todoist ; seul module qui parle à Todoist | API Todoist |
| `models` | Types pydantic : `Action`, `Proposal`, `AgentResult`, statuts | — |
| `pending_store` | Persistance SQLite des propositions et de leurs statuts | SQLite |
| `conversation_store` | Historique court (≤ 30 min, ≤ 20 messages) en SQLite | SQLite |
| `executor` | Exécute une proposition validée, action par action, et renvoie le détail des résultats | `todoist_client`, `pending_store` |

### Outils exposés à Claude

**Lecture** (exécutés immédiatement par l'agent) :
- `list_projects()`
- `search_tasks(query?, project?, due_filter?)` — `due_filter` utilise la syntaxe de filtres Todoist (`today`, `overdue`, `7 days`…)
- `get_task(task_id)`

**Écriture** (ne font rien : ils produisent une `Action` dans la proposition) :
- `propose_create_task(content, due_string?, due_datetime?, project?, priority?, labels?, description?)`
- `propose_update_task(task_id, …champs modifiés)`
- `propose_complete_task(task_id)`
- `propose_reopen_task(task_id)`
- `propose_delete_task(task_id)`

**Clarification** :
- `ask_clarification(question, options?)`

### Modèle
`claude-haiku-5-5` par défaut, configurable. La mise en cache des prompts est activée sur les instructions système et la définition des outils.

## 4. Flux de données

1. **Réception.** Vérifier `from_user.id == ALLOWED_USER_ID`, sinon ignorer et journaliser. Pour un vocal : télécharger le `.ogg`, transcrire, répondre « 🎙️ *transcription* ». Le texte suit le même chemin, sans transcription.
2. **Agent.** Le contexte envoyé à Claude contient : les instructions système (rôle limité à la gestion de tâches), la date et l'heure à Paris, la liste des projets (mise en cache 10 min), l'historique court et le message. Boucle d'outils limitée à **6 tours**. Résultats possibles :
   - `Proposal` (≥ 1 action) ;
   - `Answer` (texte, lecture seule, pas de validation) ;
   - `Clarification` (question ; la réponse de l'utilisateur repart dans le contexte).
3. **Proposition.** Valider chaque action avec pydantic, puis vérifier que le `task_id` et le projet existent. En cas d'erreur, la renvoyer à Claude pour **une** correction ; si l'erreur persiste, afficher un message d'échec. Enregistrer la proposition dans `pending_store` (statut `pending`, expiration 24 h). Afficher un résumé lisible avec les boutons `[✅ Valider] [✏️ Modifier] [❌ Annuler]` ; `callback_data` = `<verbe>:<proposal_id>`.
4. **Décision.**
   - *Valider* : passage atomique `pending → executing` (un double-clic est ignoré), exécution, puis édition du message (« ✅ Fait » et lien de la tâche, ou détail par action). Statut final : `done` ou `partial`.
   - *Annuler* : statut `cancelled`, message « ❌ Annulé ».
   - *Modifier* : statut `editing`, le bot demande « Que veux-tu changer ? ». Le message suivant est envoyé à l'agent avec la proposition d'origine. La nouvelle proposition remplace l'ancienne (`replaced`, ancien message marqué « remplacé »).
5. **Cas particuliers.** Un nouveau message pendant une proposition en attente est traité comme une nouvelle demande, et l'ancienne reste cliquable. Un clic sur une proposition expirée ou déjà traitée affiche « expirée / déjà traitée ».

### Statuts d'une proposition
`pending → executing → done | partial`, `pending → cancelled`, `pending → editing → replaced`, `pending → expired`. Seul `pending` accepte Valider, Modifier ou Annuler. `partial` accepte « Réessayer ce qui reste ».

## 5. Gestion des erreurs

Principe : ne jamais échouer en silence, et ne jamais écrire dans Todoist sans validation.

| Cas | Comportement |
|---|---|
| Audio vide / transcription vide | « Je n'ai pas compris, tu peux répéter ou l'écrire ? » |
| Panne de transcription | 1 nouvelle tentative automatique, puis message d'erreur avec `[🔁 Réessayer]` ; l'audio est gardé jusqu'au succès, puis supprimé |
| Erreur ou délai Anthropic | Tentatives du SDK (2), puis « Claude ne répond pas » avec `[🔁 Réessayer]` ; le texte est conservé |
| Boucle d'outils > 6 tours | Arrêt, message « demande trop complexe, reformule » |
| Action invalide | Renvoyée à Claude pour une correction, puis message d'échec |
| Échec Todoist pendant l'exécution | Arrêt à la première erreur ; détail ✅/❌ par action ; statut `partial` ; `[Réessayer ce qui reste]` ; pas de rollback |
| Tâche modifiée ou supprimée entre-temps | Affichée en échec explicite pour cette action |
| Double-clic | Transition atomique en SQLite : le second clic est ignoré |
| Plantage / redémarrage | systemd `Restart=always` ; propositions persistées en SQLite |
| Utilisateur inconnu | Ignoré, journalisé |

Journalisation vers journald : événements, durées, tokens consommés par appel Claude. Pas de contenu audio conservé. Un plafond de dépense est fixé dans la console Anthropic.

## 6. Coûts estimés

Hypothèse : ~900 interactions par mois, dont ~600 vocaux de 15 s ; ~10 k tokens en entrée et ~600 en sortie par interaction.

| Poste | Estimation |
|---|---|
| Claude Haiku 5.5 (0,10 $ / 0,50 $ par M tokens) | ~1,20 $/mois (moins avec le cache) |
| Groq Whisper Turbo (0,04 $/h, minimum 10 s par requête) | ~0,10 $/mois |
| Telegram, Todoist (plan gratuit), VPS existant | 0 $ |
| **Total** | **≈ 1 à 2 $/mois** |

## 7. Tests

Développement en TDD avec pytest.

1. **Unitaires**, avec des versions factices de Todoist (en mémoire), du transcripteur et de Claude (appels d'outils scriptés) : validation des actions, transitions de statut, exécuteur (succès, échec partiel, nouvelle tentative, tâche disparue), double-clic, filtrage utilisateur, expiration.
2. **Parcours complet** avec des faux : simulation d'une mise à jour Telegram → transcription → agent → proposition → Valider / Modifier / Annuler / clarification.
3. **Évaluation de la compréhension** (manuelle, vrai Claude) : ~30 phrases françaises avec les actions attendues (dates relatives, multi-actions, ambiguïtés, recherche de tâche existante). Seuil visé : ≥ 90 % correctes. Lancée à chaque changement de prompt, hors CI.
4. **Recette manuelle** sur le VPS avec un projet Todoist « Bot-test ».

## 8. Configuration (`.env`)

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USER_ID=
ANTHROPIC_API_KEY=
CLAUDE_MODEL=claude-haiku-5-5
TODOIST_API_TOKEN=
GROQ_API_KEY=
TIMEZONE=Europe/Paris
DB_PATH=./data/assistant.db
```

## 9. Pour les sous-projets suivants

L'architecture est pensée pour s'étendre sans refonte :
- de nouveaux outils pour l'agent (Gmail, Calendar) ;
- une tâche planifiée pour les briefs (B) ;
- le même mécanisme proposition/validation pour mail → tâche (C) et la création de créneaux (D).
