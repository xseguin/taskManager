# Assistant vocal de tâches (sous-projet 1) — Plan d'implémentation

> **Pour les agents :** SOUS-SKILL REQUIS : utiliser superpowers:subagent-driven-development (recommandé) ou superpowers:executing-plans pour exécuter ce plan tâche par tâche. Les étapes utilisent des cases à cocher (`- [ ]`).

**Objectif :** un bot Telegram personnel qui transforme un message vocal ou texte en propositions d'actions Todoist, exécutées seulement après validation par bouton.

**Architecture :** un service Python unique en long polling. Le cœur métier (`Controller`) ne dépend pas de Telegram : il reçoit `(user_id, texte | audio | callback)` et renvoie des `Reply`. Un adaptateur fin `python-telegram-bot` le branche. Claude (Haiku) choisit des outils : ceux de lecture sont exécutés tout de suite, ceux d'écriture produisent des `Action` regroupées dans une `Proposal` persistée en SQLite.

**Pile technique :** Python ≥ 3.11, `python-telegram-bot` ≥ 21, `anthropic`, `todoist-api-python`, `groq`, `pydantic` v2, `python-dotenv`, `sqlite3` (stdlib), `pytest`, `pytest-asyncio`.

**Spec :** `docs/superpowers/specs/2026-11-09-assistant-vocal-taches-design.md`

## Contraintes globales

- Un seul utilisateur autorisé : `TELEGRAM_ALLOWED_USER_ID` ; tout autre compte est ignoré (journalisé, aucune réponse).
- Langue : français, pour les messages du bot comme pour la transcription (`language="fr"`). Fuseau : `Europe/Paris` (variable `TIMEZONE`).
- Modèle par défaut : `claude-haiku-5-5` (variable `CLAUDE_MODEL`). Cache de prompt (`cache_control: {"type": "ephemeral"}`) sur le prompt système et les outils.
- Transcription : Groq `whisper-large-v3-turbo`.
- Aucune écriture dans Todoist sans clic sur « Valider ».
- Boucle d'outils : **6 tours maximum**. Action invalide : **1** correction demandée à Claude, puis échec.
- Expiration d'une proposition : **24 h**. Historique conversationnel : **≤ 30 min et ≤ 20 messages**. Cache de la liste des projets : **10 min**.
- Transcription : 1 nouvelle tentative automatique en cas d'erreur. Anthropic : `max_retries=2` (SDK).
- L'audio est supprimé après une transcription réussie ; aucun audio n'est écrit sur disque.
- Toutes les horloges sont injectées (`clock: Callable[[], datetime]`, avec fuseau) pour les tests.
- Variables `.env` : `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`, `ANTHROPIC_API_KEY`, `CLAUDE_MODEL`, `TODOIST_API_TOKEN`, `GROQ_API_KEY`, `TIMEZONE`, `DB_PATH` (défaut `./data/assistant.db`).
- Messages Telegram envoyés en `parse_mode=HTML` ; tout texte venant de l'utilisateur ou de Todoist est échappé avec `html.escape`.

## Points d'attention (Review Focus)

1. **« Demain » à 23 h 30, heure de Paris, sur un VPS en UTC** : la date et l'heure transmises à Claude sont en Europe/Paris, avec le jour de la semaine. *Test dans la tâche 8.*
2. **Titre de tâche contenant `<`, `&` ou `*`** : l'affichage Telegram ne casse pas (échappement HTML). *Test dans la tâche 10.*
3. **Réponse plus longue que 4 096 caractères** (par exemple « liste toutes mes tâches ») : elle est découpée en plusieurs messages au lieu d'échouer. *Test dans la tâche 10.*
4. **Recherche qui renvoie des centaines de tâches** : au maximum 30 résultats sont transmis à Claude, avec la mention « (N autres non affichés) ». *Test dans la tâche 5.*
5. **Vocal de plus de 5 minutes** : il est refusé poliment, sans transcription facturée. *Test dans la tâche 11.*

---

## Structure des fichiers

```
assistant-taches/
├── pyproject.toml
├── .env.example
├── deploy/assistant.service          # unité systemd
├── README.md                         # installation, déploiement, recette manuelle
├── assistant/
│   ├── __init__.py
│   ├── config.py                     # Config + load_config()
│   ├── models.py                     # Action, Proposal, résultats, AgentResult
│   ├── db.py                         # connexion SQLite + schéma
│   ├── pending_store.py              # propositions et statuts
│   ├── conversation_store.py         # historique court
│   ├── todoist_client.py             # TodoistPort, Task, Project, TodoistClient
│   ├── transcriber.py                # Transcriber, GroqTranscriber
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── tools.py                  # schémas d'outils + conversion en Action
│   │   ├── validation.py             # validate_actions()
│   │   ├── prompt.py                 # build_system_prompt()
│   │   └── agent.py                  # Agent.run()
│   ├── executor.py                   # Executor.execute()
│   ├── render.py                     # textes HTML, boutons, découpage
│   ├── controller.py                 # logique Telegram-agnostique
│   ├── bot.py                        # adaptateur python-telegram-bot
│   └── main.py                       # assemblage + run_polling
├── evals/
│   ├── phrases.yaml                  # ~30 cas
│   └── run_evals.py
└── tests/
    ├── conftest.py                   # horloge figée, db temporaire
    ├── fakes.py                      # FakeTodoist, FakeTranscriber, FakeClaude
    └── test_*.py
```

---

### Tâche 1 : Squelette du projet et configuration

**Fichiers :**
- Créer : `pyproject.toml`, `.env.example`, `.gitignore` (`.env`, `data/`, `__pycache__/`, `.venv/`), `assistant/__init__.py`, `assistant/config.py`, `tests/conftest.py`
- Test : `tests/test_config.py`

**Interfaces :**
- Produit : `@dataclass(frozen=True) Config(telegram_token: str, allowed_user_id: int, anthropic_api_key: str, claude_model: str, todoist_token: str, groq_api_key: str, timezone: ZoneInfo, db_path: Path)` ; `load_config(env: Mapping[str, str]) -> Config` ; `ConfigError(Exception)`.
- Produit (conftest) : la fixture `now` (`datetime(2026, 11, 9, 15, 0, tzinfo=ZoneInfo("Europe/Paris"))`, un lundi), la fixture `clock` (callable mutable, avec `clock.advance(timedelta)`) et la fixture `db_path` (`tmp_path / "t.db"`).

- [ ] **Étape 1 : écrire les tests qui échouent**

```python
BASE = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_ALLOWED_USER_ID": "42", "ANTHROPIC_API_KEY": "a",
        "TODOIST_API_TOKEN": "td", "GROQ_API_KEY": "g"}

def test_defaults():
    c = load_config(BASE)
    assert c.allowed_user_id == 42
    assert c.claude_model == "claude-haiku-5-5"
    assert c.timezone == ZoneInfo("Europe/Paris")
    assert c.db_path == Path("./data/assistant.db")

def test_missing_key_raises_with_name():
    env = {k: v for k, v in BASE.items() if k != "GROQ_API_KEY"}
    with pytest.raises(ConfigError, match="GROQ_API_KEY"):
        load_config(env)

def test_non_numeric_user_id_raises():
    with pytest.raises(ConfigError, match="TELEGRAM_ALLOWED_USER_ID"):
        load_config({**BASE, "TELEGRAM_ALLOWED_USER_ID": "abc"})
```

- [ ] **Étape 2 :** `pip install -e .[dev]` puis `pytest tests/test_config.py -v`. Attendu : ÉCHEC (import).
- [ ] **Étape 3 :** écrire `pyproject.toml` (dépendances de la pile technique ; extra `dev` = pytest, pytest-asyncio, pyyaml ; `asyncio_mode = "auto"`), `.env.example` (les 8 variables des contraintes globales) et `load_config`.
- [ ] **Étape 4 :** `pytest -v`. Attendu : SUCCÈS.
- [ ] **Étape 5 :** commit `feat: squelette du projet et configuration`.

---

### Tâche 2 : Modèles

**Fichiers :**
- Créer : `assistant/models.py`
- Test : `tests/test_models.py`

**Interfaces :**
- Produit :
  - `ActionKind(StrEnum)` : `CREATE="create"`, `UPDATE="update"`, `COMPLETE="complete"`, `REOPEN="reopen"`, `DELETE="delete"`.
  - `Action(BaseModel)` : `kind: ActionKind`, `task_id: str | None = None`, `content: str | None = None`, `due_string: str | None = None`, `project: str | None = None` (nom du projet), `priority: int | None = None` (échelle de l'API Todoist : 1 = normale … 4 = urgente), `labels: list[str] | None = None`, `description: str | None = None`, `task_label: str | None = None` (titre actuel, rempli par la validation, pour l'affichage).
    Validation : `CREATE` exige un `content` non vide ; les autres types exigent `task_id` ; `UPDATE` exige au moins un champ modifié ; `priority` est comprise entre 1 et 4.
  - `ProposalStatus(StrEnum)` : `PENDING, EXECUTING, DONE, PARTIAL, CANCELLED, EDITING, REPLACED, EXPIRED` (valeurs en minuscules).
  - `ActionResult(BaseModel)` : `index: int`, `ok: bool`, `task_id: str | None = None`, `url: str | None = None`, `error: str | None = None`.
  - `Proposal(BaseModel)` : `id: str`, `actions: list[Action]`, `status: ProposalStatus`, `source_text: str`, `created_at: datetime`, `expires_at: datetime`, `message_id: int | None = None`, `results: list[ActionResult] = []`.
  - Résultats de l'agent (dataclasses gelées) : `ProposalDraft(actions: list[Action])`, `Answer(text: str)`, `Clarification(question: str, options: list[str])`, `AgentFailure(message: str)` ; `AgentResult = ProposalDraft | Answer | Clarification | AgentFailure`.

- [ ] **Étape 1 : écrire les tests qui échouent**

```python
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
```

- [ ] **Étape 2 :** `pytest tests/test_models.py -v`. Attendu : ÉCHEC.
- [ ] **Étape 3 :** implémenter `models.py` (pydantic `model_validator(mode="after")`).
- [ ] **Étape 4 :** `pytest -v`. Attendu : SUCCÈS.
- [ ] **Étape 5 :** commit `feat: modèles Action et Proposal`.

---

### Tâche 3 : Base SQLite et `PendingStore`

**Fichiers :**
- Créer : `assistant/db.py`, `assistant/pending_store.py`
- Test : `tests/test_pending_store.py`

**Interfaces :**
- Consomme : `Action`, `Proposal`, `ProposalStatus`, `ActionResult` (tâche 2).
- Produit :
  - `connect(db_path: Path) -> sqlite3.Connection` : crée le dossier parent et les tables `proposals(id TEXT PRIMARY KEY, status TEXT, data TEXT)` et `conversation(id INTEGER PRIMARY KEY, role TEXT, content TEXT, at TEXT)` ; `isolation_level=None` (autocommit).
  - `PendingStore(conn, clock, ttl=timedelta(hours=24))` avec :
    - `create(actions: list[Action], source_text: str) -> Proposal` (id = `uuid4().hex[:12]`, statut `PENDING`) ;
    - `get(proposal_id: str) -> Proposal | None` : une proposition `PENDING` ou `EDITING` dont `expires_at` est dépassé est passée en `EXPIRED` puis renvoyée ;
    - `transition(proposal_id: str, allowed_from: set[ProposalStatus], to: ProposalStatus) -> bool` : **atomique**, par un seul `UPDATE … WHERE id=? AND status IN (…)` ; renvoie `rowcount == 1`. Elle applique d'abord l'expiration ;
    - `set_message_id(proposal_id: str, message_id: int) -> None` ;
    - `save_results(proposal_id: str, results: list[ActionResult], status: ProposalStatus) -> Proposal` ;
    - `get_editing() -> Proposal | None` : la proposition en `EDITING`, s'il y en a une.

La colonne `status` est la source de vérité ; `data` contient le JSON de la `Proposal`, mis à jour à chaque changement.

- [ ] **Étape 1 : écrire les tests qui échouent**

```python
def test_create_and_get(store):
    p = store.create([Action(kind="create", content="x")], "x")
    assert store.get(p.id).status == ProposalStatus.PENDING

def test_transition_is_exclusive(store):
    p = store.create([Action(kind="create", content="x")], "x")
    assert store.transition(p.id, {ProposalStatus.PENDING}, ProposalStatus.EXECUTING) is True
    assert store.transition(p.id, {ProposalStatus.PENDING}, ProposalStatus.EXECUTING) is False

def test_expires_after_24h(store, clock):
    p = store.create([Action(kind="create", content="x")], "x")
    clock.advance(timedelta(hours=24, seconds=1))
    assert store.get(p.id).status == ProposalStatus.EXPIRED
    assert store.transition(p.id, {ProposalStatus.PENDING}, ProposalStatus.EXECUTING) is False

def test_get_editing_and_message_id(store):
    p = store.create([Action(kind="create", content="x")], "x")
    store.set_message_id(p.id, 99)
    store.transition(p.id, {ProposalStatus.PENDING}, ProposalStatus.EDITING)
    assert store.get_editing().message_id == 99

def test_unknown_id_returns_none(store):
    assert store.get("nope") is None
```

(La fixture `store` est ajoutée à `conftest.py` : `PendingStore(connect(db_path), clock)`.)

- [ ] **Étape 2 :** `pytest tests/test_pending_store.py -v`. Attendu : ÉCHEC.
- [ ] **Étape 3 :** implémenter `db.py` et `pending_store.py`.
- [ ] **Étape 4 :** `pytest -v`. Attendu : SUCCÈS.
- [ ] **Étape 5 :** commit `feat: stockage des propositions en SQLite`.

---

### Tâche 4 : `ConversationStore`

**Fichiers :**
- Créer : `assistant/conversation_store.py`
- Test : `tests/test_conversation_store.py`

**Interfaces :**
- Consomme : `connect` (tâche 3).
- Produit : `ConversationStore(conn, clock, window=timedelta(minutes=30), max_messages=20)` avec `add(role: Literal["user", "assistant"], content: str) -> None`, `recent() -> list[dict]` (au format des messages Anthropic `{"role", "content"}`, du plus ancien au plus récent, sans aucun message `assistant` en tête de liste) et `clear() -> None`.

- [ ] **Étape 1 : écrire les tests qui échouent**

```python
def test_window_30_minutes(conv, clock):
    conv.add("user", "vieux"); clock.advance(timedelta(minutes=31))
    conv.add("user", "récent")
    assert [m["content"] for m in conv.recent()] == ["récent"]

def test_max_20_messages(conv):
    for i in range(25):
        conv.add("user" if i % 2 == 0 else "assistant", str(i))
    r = conv.recent()
    assert len(r) <= 20 and r[0]["role"] == "user" and r[-1]["content"] == "24"
```

- [ ] **Étape 2 :** lancer les tests. Attendu : ÉCHEC.
- [ ] **Étape 3 :** implémenter.
- [ ] **Étape 4 :** `pytest -v`. Attendu : SUCCÈS.
- [ ] **Étape 5 :** commit `feat: historique conversationnel court`.

---

### Tâche 5 : Client Todoist et faux Todoist

**Fichiers :**
- Créer : `assistant/todoist_client.py`, `tests/fakes.py` (avec `FakeTodoist`)
- Test : `tests/test_todoist_client.py`

**Interfaces :**
- Consomme : `Action`, `ActionKind` (tâche 2).
- Produit :
  - `@dataclass Project(id: str, name: str)` et `@dataclass Task(id: str, content: str, project_id: str, priority: int, due: str | None, labels: list[str], url: str, is_completed: bool = False)`.
  - `TodoistError(Exception)` et `TaskNotFound(TodoistError)`.
  - `class TodoistPort(Protocol)`, avec les méthodes :
    - `list_projects() -> list[Project]` ;
    - `search_tasks(query: str | None = None, project: str | None = None, due_filter: str | None = None) -> list[Task]` ;
    - `get_task(task_id: str) -> Task | None` ;
    - `create_task(action: Action) -> Task` ;
    - `update_task(action: Action) -> Task` ;
    - `complete_task(task_id: str) -> None` ;
    - `reopen_task(task_id: str) -> None` ;
    - `delete_task(task_id: str) -> None`.
  - `TodoistClient(token: str)` implémente `TodoistPort` avec `todoist_api_python.api.TodoistAPI`. Avant de coder, vérifier les noms exacts des méthodes dans la version installée du SDK (par exemple `complete_task` / `uncomplete_task` ou `close_task` / `reopen_task`, et `filter_tasks`). Le nom de projet est converti en `project_id` sans tenir compte de la casse. La recherche construit un filtre Todoist : `search: <query>`, `#<projet>` et `<due_filter>`, combinés par `&`. Une erreur HTTP 404 lève `TaskNotFound` ; toute autre erreur du SDK lève `TodoistError`.
  - `format_tasks_for_llm(tasks: list[Task], limit: int = 30) -> str` : une ligne par tâche (`[id] titre — échéance — projet — P`), puis `(N autres non affichés)` au-delà de la limite.
  - `FakeTodoist` (dans `tests/fakes.py`) : en mémoire, il implémente `TodoistPort`. `fail_on: dict[str, Exception]` (méthode → erreur) permet de simuler une panne, et `fail_after: dict[str, int]` fixe le nombre d'appels réussis avant l'échec (0 par défaut). `calls: Counter[str]` compte les appels par méthode. `tasks: dict[str, Task]`. `search_tasks` filtre par sous-chaîne, sans tenir compte de la casse.

- [ ] **Étape 1 : écrire les tests qui échouent**

```python
def test_format_caps_at_30():
    tasks = [Task(str(i), f"t{i}", "p", 1, None, [], "u") for i in range(45)]
    out = format_tasks_for_llm(tasks)
    assert out.count("\n") == 30 and out.endswith("(15 autres non affichés)")

def test_client_maps_project_name_case_insensitive():
    client = TodoistClient.__new__(TodoistClient); client._api = MagicMock()
    client._api.get_projects.return_value = <liste SDK contenant un projet id="p1", name="Clients">
    client.create_task(Action(kind="create", content="x", project="clients"))
    assert client._api.add_task.call_args.kwargs["project_id"] == "p1"

def test_client_404_raises_task_not_found():
    # client._api.get_task lève l'erreur HTTP du SDK avec status 404
    with pytest.raises(TaskNotFound):
        client.get_task("zzz")

def test_fake_todoist_contract():
    t = FakeTodoist(projects=[Project("p1", "Clients")])
    task = t.create_task(Action(kind="create", content="Devis Paul", project="Clients"))
    assert t.search_tasks(query="devis")[0].id == task.id
    t.complete_task(task.id)
    assert t.get_task(task.id).is_completed
```

- [ ] **Étape 2 :** lancer les tests. Attendu : ÉCHEC.
- [ ] **Étape 3 :** implémenter `todoist_client.py` et `FakeTodoist`.
- [ ] **Étape 4 :** `pytest -v`. Attendu : SUCCÈS.
- [ ] **Étape 5 :** commit `feat: client Todoist et faux Todoist`.

---

### Tâche 6 : Transcripteur

**Fichiers :**
- Créer : `assistant/transcriber.py` ; ajouter `FakeTranscriber` à `tests/fakes.py`
- Test : `tests/test_transcriber.py`

**Interfaces :**
- Produit :
  - `class Transcriber(Protocol)` : `transcribe(audio: bytes, filename: str) -> str` ;
  - `TranscriptionError(Exception)` ;
  - `GroqTranscriber(client, model="whisper-large-v3-turbo")`, où `client` est un `groq.Groq` injecté. Appel : `client.audio.transcriptions.create(file=(filename, audio), model=model, language="fr")`. Le texte renvoyé est nettoyé de ses espaces (`.strip()`). En cas d'exception, **1** nouvelle tentative, puis `TranscriptionError`.
  - `FakeTranscriber(results: list[str | Exception])` : renvoie ou lève les éléments dans l'ordre.

- [ ] **Étape 1 : écrire les tests qui échouent**

```python
def test_groq_called_in_french():
    c = MagicMock(); c.audio.transcriptions.create.return_value.text = "  bonjour "
    assert GroqTranscriber(c).transcribe(b"a", "v.ogg") == "bonjour"
    assert c.audio.transcriptions.create.call_args.kwargs["language"] == "fr"

def test_retries_once_then_raises():
    c = MagicMock(); c.audio.transcriptions.create.side_effect = RuntimeError("down")
    with pytest.raises(TranscriptionError):
        GroqTranscriber(c).transcribe(b"a", "v.ogg")
    assert c.audio.transcriptions.create.call_count == 2
```

- [ ] **Étape 2 :** lancer les tests. Attendu : ÉCHEC.
- [ ] **Étape 3 :** implémenter.
- [ ] **Étape 4 :** `pytest -v`. Attendu : SUCCÈS.
- [ ] **Étape 5 :** commit `feat: transcription Groq`.

---

### Tâche 7 : Outils de l'agent et validation des actions

**Fichiers :**
- Créer : `assistant/agent/__init__.py`, `assistant/agent/tools.py`, `assistant/agent/validation.py`
- Test : `tests/test_agent_tools.py`

**Interfaces :**
- Consomme : `Action` (tâche 2), `TodoistPort` et `TaskNotFound` (tâche 5).
- Produit :
  - `TOOLS: list[dict]` : schémas Anthropic pour `list_projects`, `search_tasks(query?, project?, due_filter?)`, `get_task(task_id)`, `propose_create_task(content, due_string?, project?, priority?, labels?, description?)`, `propose_update_task(task_id, content?, due_string?, project?, priority?, labels?, description?)`, `propose_complete_task(task_id)`, `propose_reopen_task(task_id)`, `propose_delete_task(task_id)` et `ask_clarification(question, options?)`. Les descriptions sont en français ; celle de `priority` précise « 4 = urgente, 1 = normale » ; celle de `due_string` précise « en langage naturel compris par Todoist, en anglais de préférence (ex. "thursday 18:00") ». Le cache est posé sur le **dernier** outil (`cache_control`).
  - `READ_TOOLS = {"list_projects", "search_tasks", "get_task"}` et `WRITE_TOOLS = {"propose_create_task", "propose_update_task", "propose_complete_task", "propose_reopen_task", "propose_delete_task"}`.
  - `action_from_tool(name: str, tool_input: dict) -> Action` : le nom de l'outil donne `ActionKind` ; lève `ValueError` (message lisible) si pydantic refuse.
  - `run_read_tool(name: str, tool_input: dict, todoist: TodoistPort) -> str` : texte destiné à Claude (s'appuie sur `format_tasks_for_llm`).
  - `validate_actions(actions: list[Action], todoist: TodoistPort) -> list[str]` : renvoie une liste d'erreurs en français, vide si tout est valide. Vérifie que chaque `task_id` existe (et remplit `action.task_label` avec son titre) et que chaque `project` existe. Rejette `COMPLETE` sur une tâche déjà terminée et `REOPEN` sur une tâche non terminée.

- [ ] **Étape 1 : écrire les tests qui échouent**

```python
def test_action_from_tool_create():
    a = action_from_tool("propose_create_task", {"content": "Devis", "priority": 3})
    assert a.kind == ActionKind.CREATE and a.priority == 3

def test_action_from_tool_invalid_raises():
    with pytest.raises(ValueError):
        action_from_tool("propose_complete_task", {})

def test_validate_unknown_task_and_project():
    t = FakeTodoist(projects=[Project("p1", "Clients")])
    errs = validate_actions([Action(kind="complete", task_id="zzz"),
                             Action(kind="create", content="x", project="Inconnu")], t)
    assert len(errs) == 2 and "zzz" in errs[0] and "Inconnu" in errs[1]

def test_validate_fills_task_label():
    t = FakeTodoist(projects=[Project("p1", "Clients")])
    task = t.create_task(Action(kind="create", content="Devis Paul"))
    a = Action(kind="complete", task_id=task.id)
    assert validate_actions([a], t) == [] and a.task_label == "Devis Paul"

def test_tools_last_has_cache_control():
    assert TOOLS[-1]["cache_control"] == {"type": "ephemeral"}
```

- [ ] **Étape 2 :** lancer les tests. Attendu : ÉCHEC.
- [ ] **Étape 3 :** implémenter.
- [ ] **Étape 4 :** `pytest -v`. Attendu : SUCCÈS.
- [ ] **Étape 5 :** commit `feat: outils et validation de l'agent`.

---

### Tâche 8 : Prompt système et boucle de l'agent

**Fichiers :**
- Créer : `assistant/agent/prompt.py`, `assistant/agent/agent.py` ; ajouter `FakeClaude` à `tests/fakes.py`
- Test : `tests/test_agent.py`

**Interfaces :**
- Consomme : tâches 2, 4, 5 et 7.
- Produit :
  - `build_system_prompt(now: datetime, projects: list[Project]) -> list[dict]` : un bloc texte avec `cache_control` pour les instructions fixes, puis un bloc **sans** cache pour la date du jour. Les instructions fixes indiquent :
    - le rôle (assistant de gestion de tâches Todoist, rien d'autre ; poliment, il refuse ce qui sort du sujet) ;
    - qu'il faut toujours chercher une tâche existante avant de proposer une modification ;
    - qu'en cas d'ambiguïté entre plusieurs tâches, il faut appeler `ask_clarification` ;
    - qu'il répond en français, brièvement.
    Le bloc de la date donne la date, l'heure et le jour de la semaine en Europe/Paris (par exemple « lundi 9 novembre 2026, 23:30 (Europe/Paris) ») et la liste des projets.
  - `Agent(client, todoist: TodoistPort, conversation: ConversationStore, model: str, clock, project_cache_ttl=timedelta(minutes=10))` ;
  - `Agent.run(text: str, editing: Proposal | None = None) -> AgentResult` ;
  - `MAX_TURNS = 6`.

Algorithme de `run` (non déterminé par les signatures) :

```
messages = conversation.recent() + [user(text, avec, si editing, le résumé de la proposition d'origine)]
corrections = 0
pour tour in 1..MAX_TURNS :
    r = client.messages.create(model, max_tokens=1024, system=build_system_prompt(...), tools=TOOLS, messages)
    journaliser r.usage
    calls = blocs tool_use de r
    si un appel ask_clarification → return Clarification
    writes = appels dans WRITE_TOOLS
    si writes :
        convertir (action_from_tool) + validate_actions ; si aucune erreur → return ProposalDraft
        si corrections == 1 → return AgentFailure("Je n'ai pas réussi à préparer une action valide : <erreurs>")
        corrections += 1 ; renvoyer tool_result is_error=True (erreurs) pour chaque write, "ok" pour les autres ; continuer
    sinon si calls (lecture) : exécuter run_read_tool, ajouter assistant + tool_results ; continuer
    sinon : return Answer(texte de r)
return AgentFailure("Demande trop complexe, reformule-la.")
```

Une fois le résultat obtenu, `run` ajoute à `conversation` le texte de l'utilisateur et un résumé textuel du résultat. Les erreurs `anthropic.APIError` sont **propagées** (le contrôleur les gère).

- `FakeClaude(responses: list)` : il expose `messages.create(**kw)`, qui renvoie les réponses dans l'ordre et enregistre `kw` dans `calls`. Fonctions utilitaires : `tool_use(name, input, id=None)`, `text(s)` et `response(*blocks)` (objets simples avec `.content`, `.stop_reason` et `.usage`).

- [ ] **Étape 1 : écrire les tests qui échouent**

```python
def test_prompt_uses_paris_time_late_evening():
    now = datetime(2026, 11, 9, 23, 30, tzinfo=ZoneInfo("Europe/Paris"))
    s = " ".join(b["text"] for b in build_system_prompt(now, []))
    assert "lundi 9 novembre 2026" in s and "23:30" in s

def test_read_then_propose(agent_factory):
    claude = FakeClaude([response(tool_use("search_tasks", {"query": "devis"})),
                         response(tool_use("propose_complete_task", {"task_id": "<id devis>"}))])
    # (le test crée la tâche « Devis Paul » dans FakeTodoist et injecte son id)
    res = agent_factory(claude).run("le devis Paul c'est fait")
    assert isinstance(res, ProposalDraft) and res.actions[0].task_label == "Devis Paul"
    assert len(claude.calls) == 2

def test_text_only_is_answer(agent_factory):
    res = agent_factory(FakeClaude([response(text("Rien aujourd'hui."))])).run("quoi aujourd'hui ?")
    assert res == Answer("Rien aujourd'hui.")

def test_clarification(agent_factory):
    res = agent_factory(FakeClaude([response(tool_use("ask_clarification",
          {"question": "Lequel ?", "options": ["A", "B"]}))])).run("le devis")
    assert res == Clarification("Lequel ?", ["A", "B"])

def test_one_correction_then_failure(agent_factory):
    bad = response(tool_use("propose_complete_task", {"task_id": "zzz"}))
    claude = FakeClaude([bad, bad])
    res = agent_factory(claude).run("x")
    assert isinstance(res, AgentFailure) and len(claude.calls) == 2

def test_turn_limit(agent_factory):
    claude = FakeClaude([response(tool_use("list_projects", {}))] * 7)
    res = agent_factory(claude).run("x")
    assert res == AgentFailure("Demande trop complexe, reformule-la.") and len(claude.calls) == 6

def test_projects_cached_10_minutes(agent_factory, clock, fake_todoist):
    claude = FakeClaude([response(text("ok"))] * 3)
    agent = agent_factory(claude)
    agent.run("a"); clock.advance(timedelta(minutes=5)); agent.run("b")
    assert fake_todoist.calls["list_projects"] == 1
    clock.advance(timedelta(minutes=6)); agent.run("c")
    assert fake_todoist.calls["list_projects"] == 2
```

- [ ] **Étape 2 :** `pytest tests/test_agent.py -v`. Attendu : ÉCHEC.
- [ ] **Étape 3 :** implémenter `prompt.py` (noms français des jours et des mois écrits en dur, sans dépendre de la locale système), `agent.py` et `FakeClaude`.
- [ ] **Étape 4 :** `pytest -v`. Attendu : SUCCÈS.
- [ ] **Étape 5 :** commit `feat: boucle agent Claude`.

---

### Tâche 9 : Exécuteur

**Fichiers :**
- Créer : `assistant/executor.py`
- Test : `tests/test_executor.py`

**Interfaces :**
- Consomme : `PendingStore` (tâche 3), `TodoistPort`, `TodoistError` et `TaskNotFound` (tâche 5).
- Produit : `Executor(todoist: TodoistPort, store: PendingStore)` avec `execute(proposal_id: str, retry: bool = False) -> Proposal | None`.
  - Il passe la proposition `PENDING → EXECUTING` (ou, si `retry=True`, `PARTIAL → EXECUTING`). Si la transition échoue, il renvoie `None` : double-clic, proposition expirée ou déjà traitée.
  - Il exécute les actions dans l'ordre, en sautant les indices déjà `ok` dans `results`, et s'arrête à la première erreur.
  - Messages d'erreur : `TaskNotFound` → « tâche introuvable (modifiée ou supprimée entre-temps) » ; autre `TodoistError` → « erreur Todoist : <message> ».
  - Statut final : `DONE` si toutes les actions sont `ok`, sinon `PARTIAL`. Il sauvegarde via `save_results` et renvoie la proposition.
  - `create_task` et `update_task` renseignent `task_id` et `url` dans `ActionResult`.

- [ ] **Étape 1 : écrire les tests qui échouent**

```python
def test_executes_all(executor, store, fake_todoist):
    p = store.create([Action(kind="create", content="A"), Action(kind="create", content="B")], "x")
    done = executor.execute(p.id)
    assert done.status == ProposalStatus.DONE and len(fake_todoist.tasks) == 2

def test_double_click_returns_none(executor, store):
    p = store.create([Action(kind="create", content="A")], "x")
    executor.execute(p.id)
    assert executor.execute(p.id) is None

def test_partial_then_retry_remaining(executor, store, fake_todoist):
    p = store.create([Action(kind="create", content="A"), Action(kind="create", content="B")], "x")
    fake_todoist.fail_on["create_task"] = TodoistError("503")
    fake_todoist.fail_after["create_task"] = 1   # 1er appel OK, 2e en échec
    r = executor.execute(p.id)
    assert r.status == ProposalStatus.PARTIAL and [x.ok for x in r.results] == [True, False]
    fake_todoist.fail_on.clear()
    r2 = executor.execute(p.id, retry=True)
    assert r2.status == ProposalStatus.DONE and len(fake_todoist.tasks) == 2  # A n'est pas recréée

def test_task_vanished(executor, store):
    p = store.create([Action(kind="complete", task_id="gone")], "x")
    r = executor.execute(p.id)
    assert "introuvable" in r.results[0].error
```

- [ ] **Étape 2 :** lancer les tests. Attendu : ÉCHEC.
- [ ] **Étape 3 :** implémenter.
- [ ] **Étape 4 :** `pytest -v`. Attendu : SUCCÈS.
- [ ] **Étape 5 :** commit `feat: exécuteur des propositions`.

---

### Tâche 10 : Rendu des messages

**Fichiers :**
- Créer : `assistant/render.py`
- Test : `tests/test_render.py`

**Interfaces :**
- Consomme : `Proposal`, `Action` et `ActionResult` (tâche 2).
- Produit :
  - `@dataclass(frozen=True) Button(text: str, data: str)` ;
  - `render_proposal(p: Proposal) -> str` : HTML, une ligne d'en-tête par action. Le titre est `content` pour une création, `task_label` sinon :
    - 📝 **Créer** ;
    - ✏️ **Modifier** (n'affiche que les champs modifiés) ;
    - ✅ **Terminer** ;
    - ↩️ **Rouvrir** ;
    - 🗑️ **Supprimer**.
    Ensuite, si renseignés : `📅 <due_string>`, `📁 <projet>` et la priorité (API 4 → `🔴 P1`, 3 → `🟠 P2`, 2 → `🔵 P3`, 1 → rien), plus `🏷️ <étiquettes>`.
  - `proposal_buttons(proposal_id: str) -> list[Button]` → `[Button("✅ Valider", "ok:<id>"), Button("✏️ Modifier", "edit:<id>"), Button("❌ Annuler", "cancel:<id>")]` ;
  - `render_results(p: Proposal) -> str` :
    - `DONE` → « ✅ Fait », suivi de la proposition et d'un lien `<a href>` par tâche créée ;
    - `PARTIAL` → détail ✅/❌ par action, avec l'erreur ;
    - `CANCELLED` → « ❌ Annulé » ; `REPLACED` → « ↪️ Remplacé » ; `EXPIRED` → « ⌛ Expirée ».
    Le texte de la proposition est conservé sous la ligne d'état.
  - `retry_remaining_button(proposal_id) -> Button("🔁 Réessayer ce qui reste", "retry:<id>")` ;
  - `split_message(text: str, limit: int = 4096) -> list[str]` : coupe sur les sauts de ligne, et en dur si une ligne dépasse la limite.

Callback data : `<verbe>:<id>`, avec `verbe ∈ {ok, edit, cancel, retry, again}`.

- [ ] **Étape 1 : écrire les tests qui échouent**

```python
def test_create_line_and_escaping(make_proposal):
    p = make_proposal([Action(kind="create", content="Lire <Dune> & co", due_string="jeudi",
                              project="Clients", priority=3)])
    s = render_proposal(p)
    assert "📝 <b>Créer</b>" in s and "Lire &lt;Dune&gt; &amp; co" in s
    assert "📅 jeudi" in s and "📁 Clients" in s and "🟠 P2" in s

def test_buttons():
    assert [b.data for b in proposal_buttons("abc")] == ["ok:abc", "edit:abc", "cancel:abc"]

def test_partial_results(make_proposal):
    p = make_proposal([Action(kind="create", content="A"), Action(kind="create", content="B")],
                      status="partial", results=[ActionResult(index=0, ok=True, url="https://t/1"),
                                                 ActionResult(index=1, ok=False, error="erreur Todoist : 503")])
    s = render_results(p)
    assert "✅" in s and "❌" in s and "503" in s

def test_split_long_message():
    parts = split_message("ligne\n" * 1500)
    assert len(parts) >= 3 and all(len(x) <= 4096 for x in parts)
    assert "".join(parts).count("ligne") == 1500
```

- [ ] **Étape 2 :** lancer les tests. Attendu : ÉCHEC.
- [ ] **Étape 3 :** implémenter.
- [ ] **Étape 4 :** `pytest -v`. Attendu : SUCCÈS.
- [ ] **Étape 5 :** commit `feat: rendu des messages Telegram`.

---

### Tâche 11 : Contrôleur (logique indépendante de Telegram)

**Fichiers :**
- Créer : `assistant/controller.py`
- Test : `tests/test_controller.py` (parcours complets avec des faux)

**Interfaces :**
- Consomme : `Transcriber` et `TranscriptionError` (tâche 6), `Agent` (tâche 8), `PendingStore` (tâche 3), `Executor` (tâche 9), le rendu (tâche 10) et les modèles (tâche 2).
- Produit :
  - `@dataclass Reply(text: str, buttons: list[Button] = [], edit_message_id: int | None = None, proposal_id: str | None = None)` : si `edit_message_id` est renseigné, le message existant est modifié au lieu d'en envoyer un nouveau ; si `proposal_id` est renseigné, l'adaptateur appelle `store.set_message_id(proposal_id, <id envoyé>)`. Les textes longs sont découpés par l'adaptateur.
  - `Controller(allowed_user_id: int, transcriber: Transcriber, agent: Agent, store: PendingStore, executor: Executor, max_voice_seconds: int = 300)`, avec :
    - `handle_text(user_id: int, text: str) -> list[Reply]` ;
    - `handle_voice(user_id: int, audio: bytes, duration_s: int) -> list[Reply]` ;
    - `handle_callback(user_id: int, data: str, message_id: int) -> list[Reply]`.

Comportements à respecter (textes exacts) :
- Utilisateur ≠ `allowed_user_id` : renvoyer `[]` et journaliser un avertissement.
- Vocal de plus de 300 s : `"🎙️ Vocal trop long (5 min max). Tu peux le découper ?"` ; le transcripteur n'est pas appelé.
- Transcription vide : `"Je n'ai pas compris, tu peux répéter ou l'écrire ?"`.
- `TranscriptionError` : `"⚠️ La transcription a échoué."` avec le bouton `Button("🔁 Réessayer", "again:<clé>")`. L'audio est gardé en mémoire (`dict[clé, ("voice", bytes)]`) jusqu'au succès ou au redémarrage.
- Transcription réussie : d'abord la réponse `"🎙️ <i>« texte »</i>"`, puis le même traitement que `handle_text`.
- Traitement d'un texte :
  - s'il existe une proposition `EDITING` (`store.get_editing()`), appeler `agent.run(text, editing=p)` ; si le résultat est un `ProposalDraft`, passer l'ancienne en `REPLACED` et ajouter `Reply(render_results(ancienne), edit_message_id=ancienne.message_id)` ;
  - `ProposalDraft` : `store.create(...)`, puis `Reply(render_proposal, proposal_buttons, proposal_id=...)` ;
  - `Answer` : `Reply(text échappé)` ;
  - `Clarification` : `Reply("❓ question" + options en liste)` ;
  - `AgentFailure` : `Reply("⚠️ " + message)` ;
  - `anthropic.APIError` : `"⚠️ Claude ne répond pas."` avec le bouton `again:<clé>` (le texte est conservé en mémoire).
- Callbacks :
  - `ok:<id>` : `executor.execute`. Si le résultat est `None`, répondre `Reply("Cette proposition est expirée ou déjà traitée.")`. Sinon, modifier le message avec `render_results` ; si le statut est `PARTIAL`, ajouter `retry_remaining_button`.
  - `cancel:<id>` : transition `PENDING → CANCELLED`, puis modifier le message.
  - `edit:<id>` : transition `PENDING → EDITING`, puis `Reply("✏️ Que veux-tu changer ?")`.
  - `retry:<id>` : `executor.execute(id, retry=True)`.
  - `again:<clé>` : relancer l'entrée conservée.
  - Une transition refusée produit toujours `"Cette proposition est expirée ou déjà traitée."`.

- [ ] **Étape 1 : écrire les tests qui échouent** (fixture `ctl` = Controller câblé avec `FakeTranscriber`, un vrai `Agent` sur `FakeClaude`, `FakeTodoist`, `PendingStore` et `Executor`)

```python
def test_stranger_ignored(ctl):
    assert ctl.handle_text(7, "salut") == []

def test_voice_too_long_not_transcribed(ctl, fake_transcriber):
    r = ctl.handle_voice(42, b"a", duration_s=301)
    assert "trop long" in r[0].text and fake_transcriber.calls == 0

def test_voice_to_proposal_to_validate(ctl, fake_claude, fake_todoist):
    fake_claude.push(response(tool_use("propose_create_task", {"content": "Devis Paul", "due_string": "thursday"})))
    replies = ctl.handle_voice(42, b"a", duration_s=8)       # FakeTranscriber → "devis paul jeudi"
    assert replies[0].text.startswith("🎙️") and replies[1].buttons[0].data.startswith("ok:")
    pid = replies[1].proposal_id
    done = ctl.handle_callback(42, f"ok:{pid}", message_id=10)
    assert done[0].edit_message_id == 10 and "✅ Fait" in done[0].text
    assert fake_todoist.search_tasks(query="devis")

def test_double_click_no_duplicate(ctl, fake_todoist, ...):
    # deux ok:<id> successifs → 1 seule tâche ; 2e réponse = "expirée ou déjà traitée"

def test_edit_flow_replaces_old(ctl, fake_claude, store):
    # proposition → edit:<id> → "Que veux-tu changer ?" → texte "plutôt vendredi"
    # → nouvelle proposition + Reply(edit_message_id=ancien message_id, texte contenant "Remplacé")
    # → store.get(ancien).status == REPLACED

def test_transcription_failure_then_again(ctl, fake_transcriber):
    # FakeTranscriber([TranscriptionError(), "bonjour"]) → bouton again:<clé> → callback → "🎙️ <i>« bonjour »</i>"

def test_claude_down_offers_retry(ctl, fake_claude):
    # FakeClaude lève anthropic.APIConnectionError → "Claude ne répond pas." + bouton again:

def test_cancel(ctl, ...):
    # cancel:<id> → message modifié "❌ Annulé" ; un ok:<id> ensuite → "expirée ou déjà traitée"
```

Tous les tests marqués en commentaire doivent être écrits entièrement avec les assertions indiquées.

- [ ] **Étape 2 :** `pytest tests/test_controller.py -v`. Attendu : ÉCHEC.
- [ ] **Étape 3 :** implémenter `controller.py`.
- [ ] **Étape 4 :** `pytest -v`. Attendu : SUCCÈS (toute la suite).
- [ ] **Étape 5 :** commit `feat: contrôleur et parcours complets`.

---

### Tâche 12 : Adaptateur Telegram, assemblage et déploiement

**Fichiers :**
- Créer : `assistant/bot.py`, `assistant/main.py`, `deploy/assistant.service`, `README.md`
- Test : `tests/test_bot.py`

**Interfaces :**
- Consomme : `Controller`, `Reply`, `Button`, `split_message`, `PendingStore.set_message_id`, `load_config`.
- Produit :
  - `async send_replies(bot, chat_id: int, replies: list[Reply], store: PendingStore) -> None` :
    - si `edit_message_id` est renseigné : `bot.edit_message_text(...)` ;
    - sinon : `bot.send_message(...)` en `parse_mode="HTML"`, avec un `InlineKeyboardMarkup` d'une rangée de boutons si `buttons` est renseigné ; un texte long est découpé avec `split_message`, et les boutons sont attachés au dernier morceau ;
    - si `proposal_id` est renseigné : `store.set_message_id(proposal_id, sent.message_id)`.
  - `build_application(config: Config, controller: Controller, store: PendingStore) -> telegram.ext.Application` : des handlers texte, vocal (`voice.get_file()` puis `download_as_bytearray()`, `duration`) et `CallbackQueryHandler` (avec `query.answer()`). Les appels au contrôleur, qui sont synchrones, passent par `asyncio.to_thread`.
  - `main()` :
    - charger le `.env` (`dotenv`) et appeler `load_config(os.environ)` ;
    - configurer le logging (niveau INFO, vers stdout, donc journald) ;
    - instancier `anthropic.Anthropic(max_retries=2)`, `groq.Groq`, `TodoistClient`, les stores, `Agent`, `Executor` et `Controller` ;
    - lancer `app.run_polling()`.
  - `deploy/assistant.service` : `Restart=always`, `WorkingDirectory=/opt/assistant-taches`, `EnvironmentFile=/opt/assistant-taches/.env`, `ExecStart=/opt/assistant-taches/.venv/bin/python -m assistant.main`, un utilisateur système dédié.

- [ ] **Étape 1 : écrire les tests qui échouent**

```python
async def test_send_sets_message_id_and_buttons(store):
    bot = AsyncMock(); bot.send_message.return_value.message_id = 55
    p = store.create([Action(kind="create", content="x")], "x")
    await send_replies(bot, 1, [Reply("t", [Button("✅ Valider", f"ok:{p.id}")], proposal_id=p.id)], store)
    kw = bot.send_message.call_args.kwargs
    assert kw["parse_mode"] == "HTML" and kw["reply_markup"].inline_keyboard[0][0].callback_data == f"ok:{p.id}"
    assert store.get(p.id).message_id == 55

async def test_edit_reply_uses_edit(store):
    bot = AsyncMock()
    await send_replies(bot, 1, [Reply("✅ Fait", edit_message_id=10)], store)
    assert bot.edit_message_text.call_args.kwargs["message_id"] == 10

async def test_long_text_split(store):
    bot = AsyncMock()
    await send_replies(bot, 1, [Reply("x\n" * 5000)], store)
    assert bot.send_message.call_count >= 3
```

- [ ] **Étape 2 :** `pytest tests/test_bot.py -v`. Attendu : ÉCHEC.
- [ ] **Étape 3 :** implémenter `bot.py`, `main.py`, l'unité systemd et le `README.md`. Le README couvre :
  - la création du bot via @BotFather ;
  - l'obtention de son identifiant Telegram ;
  - les clés Todoist, Anthropic (avec le **plafond de dépense** à fixer dans la console) et Groq ;
  - l'installation dans `/opt/assistant-taches` et `systemctl enable --now` ;
  - la **recette manuelle** (étape 5).
- [ ] **Étape 4 :** `pytest -v`. Attendu : SUCCÈS. Puis `python -c "import assistant.main"`. Attendu : aucune erreur.
- [ ] **Étape 5 : recette manuelle sur le VPS** (liste à copier dans le README), avec un projet Todoist « Bot-test » :
  1. Vocal « ajoute acheter du pain demain dans Bot-test » → transcription affichée, puis proposition, Valider, tâche visible dans Todoist.
  2. « Le pain c'est fait » → proposition Terminer → Valider.
  3. Proposition → Modifier → « plutôt vendredi » → ancien message « Remplacé », puis nouvelle proposition.
  4. Proposition → Annuler.
  5. Double-clic rapide sur Valider → une seule tâche.
  6. « Qu'est-ce que j'ai aujourd'hui ? » → réponse sans bouton.
  7. Message depuis un autre compte Telegram → aucune réponse.
  8. `systemctl restart assistant`, puis clic sur un bouton d'une proposition antérieure → fonctionne.
- [ ] **Étape 6 :** commit `feat: adaptateur Telegram, assemblage et déploiement`.

---

### Tâche 13 : Évaluation de la compréhension

**Fichiers :**
- Créer : `evals/phrases.yaml`, `evals/run_evals.py`
- Test : `tests/test_evals_scoring.py`

**Interfaces :**
- Consomme : `Agent` (tâche 8), `FakeTodoist` (préchargé), `ProposalDraft`, `Answer` et `Clarification`.
- Produit :
  - `phrases.yaml` : une liste de cas `{phrase, now (ISO, Europe/Paris), expect}`. `expect` vaut l'une des formes suivantes :
    - `{type: proposal, actions: [{kind, content_contains?, due_date?, project?, priority?, task?}]}` ;
    - `{type: answer}` ;
    - `{type: clarification}`.
    Le fichier commence par des tâches de départ communes (`seed_tasks`), dont « Devis Paul Martin », « Devis Paul (cuisine) », « Appeler maman » et « Rapport trimestriel ».
    Il contient au moins 30 cas, dont : dates relatives (« demain », « jeudi », « dans 2 semaines », « ce soir 18 h ») ; un cas à 23 h 30 ; un projet nommé ; une priorité (« urgent ») ; 3 cas multi-actions ; 4 cas « c'est fait » ou « décale » sur une tâche existante ; 2 ambiguïtés (« le devis Paul » doit donner une clarification) ; 3 questions ; 2 demandes hors sujet (réponse de refus poli).
  - `score_case(expect: dict, result: AgentResult, todoist: FakeTodoist, now: datetime) -> tuple[bool, str]` : compare une à une les actions attendues et obtenues. Pour `due_date`, il traduit `due_string` en date avec la fonction `resolve_due` du script. La traduction est minimale : jours de la semaine, `today`, `tomorrow`, `in N days/weeks`, date ISO. Une `due_string` non reconnue compte comme un échec, avec la raison.
  - `run_evals.py` : il utilise le vrai Claude (`CLAUDE_MODEL`), affiche chaque échec avec sa raison, le taux de réussite et le coût total estimé (à partir de `usage`). Code de sortie 1 si le taux est inférieur à **90 %**.

- [ ] **Étape 1 : écrire les tests qui échouent** pour `score_case` et `resolve_due`

```python
def test_resolve_due_thursday(now):  # now = lundi 9/11/2026
    assert resolve_due("thursday", now) == date(2026, 11, 12)

def test_score_proposal_match(now):
    ok, _ = score_case({"type": "proposal", "actions": [{"kind": "create", "content_contains": "pain",
                         "due_date": "2026-11-10"}]},
                       ProposalDraft([Action(kind="create", content="Acheter du pain", due_string="tomorrow")]),
                       FakeTodoist(), now)
    assert ok

def test_score_wrong_type(now):
    ok, why = score_case({"type": "clarification"}, Answer("x"), FakeTodoist(), now)
    assert not ok and "clarification" in why
```

- [ ] **Étape 2 :** lancer les tests. Attendu : ÉCHEC.
- [ ] **Étape 3 :** implémenter le script et rédiger les 30 cas.
- [ ] **Étape 4 :** `pytest -v`. Attendu : SUCCÈS. Puis `python evals/run_evals.py` avec la vraie clé. Attendu : ≥ 90 % et un coût affiché de quelques centimes. Si le taux est inférieur, ajuster `prompt.py` ou les descriptions des outils (pas les cas), puis relancer.
- [ ] **Étape 5 :** commit `feat: jeu d'évaluation de la compréhension`.
