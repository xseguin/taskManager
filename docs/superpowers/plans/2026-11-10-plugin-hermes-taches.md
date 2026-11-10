# Plugin Hermes « tâches » — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal :** transformer taskManager en plugin Hermes : validation des vocaux par boutons, `/t` pour les tâches tapées, outils Todoist dont les écritures n'ont lieu qu'au clic, outils de l'agent isolés dans Docker.

**Architecture :** un cœur Python pur et testable (`assistant/hermes/`), qui ne connaît ni Hermes ni python-telegram-bot et parle à des ports (`Messenger`, `Injector`). Une fine couche de colle (`hermes_plugin/__init__.py`) branche ce cœur sur l'API de plugins Hermes v0.23.1 (`register_platform_handler`, hooks, `register_tool`, `register_system_prompt_section`). Le code v1 réutilisé (`todoist_client`, `models`, `pending_store`, `executor`, `render`, validation) reste en place, et la boucle Claude ainsi que le bot autonome sont supprimés.

**Tech Stack :** Python ≥ 3.11 (VPS : 3.14), pydantic 2, todoist-api-python, python-telegram-bot 22.x (fourni par Hermes), pytest + pytest-asyncio, Docker rootless.

**Spec :** `docs/superpowers/specs/2026-11-10-plugin-hermes-taches-design.md` (et la spec v1 `2026-11-09-assistant-vocal-taches-design.md` pour les règles métier reprises).

## Global Constraints

- Aucune écriture Todoist hors de `Executor.execute`, appelé uniquement depuis un clic « Valider » / « Réessayer ce qui reste ».
- Préfixe de **tous** les `callback_data` du plugin : `ta:` (vocal : `ta:v:<verbe>:<clé>`, proposition : `ta:p:<verbe>:<id>`). Le `CallbackQueryHandler` est enregistré avec `pattern=r"^ta:"`.
- Clé de session Hermes d'un chat privé : `agent:main:telegram:dm:<chat_id>` ; en message privé, `chat_id == user_id`.
- Utilisateurs autorisés : variable `TELEGRAM_ALLOWED_USERS` (liste séparée par des virgules), déjà présente dans `~/.hermes/.env`. Les cartes sont toujours envoyées en message privé au premier ID de cette liste.
- Marqueur de tâche (texte exact) : `[Créer une tâche Todoist] ` suivi du texte.
- Mot de repli (comparaison sans casse ni espaces autour) : `ok`.
- Durée de vie d'une carte vocale : 24 h. Délai de confirmation de réception par Hermes : 180 s.
- Dépendances : s'aligner sur l'environnement Hermes (python-telegram-bot 22.8, pydantic 2.14). Ne pas épingler ces deux paquets ; seule dépendance déclarée en plus : `todoist-api-python`.
- N'utiliser que les API Hermes documentées, sauf `tools.transcription_tools.transcribe_audio`, isolée dans une seule fonction de la colle.
- Textes visibles en français, en HTML Telegram (`parse_mode="HTML"`), avec échappement via `html.escape`.
- L'utilisateur `hermes` n'a pas sudo et ne doit pas l'obtenir (pas de groupe `docker`). Les opérations root passent par le compte SSH `claude` (`ssh hermes-vps`).

## Review Focus

1. **Texte transcrit contenant du HTML ou des chevrons** (« a < b ») : la carte s'affiche sans erreur Telegram de parsing. Test `test_voice_card_escapes_html` (Tâche 3).
2. **Double clic sur « Discuter »** : un seul `inject` a lieu. Test `test_double_send_injects_once` (Tâche 3).
3. **« ok » tapé sans repli armé** : il part à Hermes tel quel, sans réécriture. Test `test_ok_without_pending_is_untouched` (Tâche 2).
4. **Message d'un utilisateur non autorisé commençant par `/t` ou valant `ok`** : aucune réécriture et aucune fuite d'une transcription en attente. Test `test_rewrite_ignores_unauthorized_user` (Tâche 2).
5. **Proposition remplacée ou expirée, puis clic sur l'ancienne carte** : « expirée ou déjà traitée », sans exécution. Test `test_click_on_replaced_proposal_refused` (Tâche 4).

---

### Task 1 : Retirer le bot autonome v1 et préparer le paquet

**Files :**
- Delete : `assistant/agent/agent.py`, `assistant/agent/prompt.py`, `assistant/agent/tools.py`, `assistant/agent/__init__.py`, `assistant/bot.py`, `assistant/main.py`, `assistant/controller.py`, `assistant/conversation_store.py`, `assistant/transcriber.py`, `deploy/assistant.service`, `evals/`, `.env.example`, `tests/test_agent.py`, `tests/test_agent_tools.py`, `tests/test_bot.py`, `tests/test_controller.py`, `tests/test_conversation_store.py`, `tests/test_transcriber.py`, `tests/test_evals_scoring.py`
- Move : `assistant/agent/validation.py` → `assistant/validation.py` (imports adaptés dans le code et `tests/`)
- Modify : `assistant/models.py` (supprimer `ProposalDraft`, `Answer`, `Clarification`, `AgentFailure`, `AgentResult`), `assistant/db.py` (supprimer la table `conversation`), `assistant/config.py`, `tests/test_config.py`, `tests/fakes.py` (supprimer `FakeTranscriber` et le faux Claude), `pyproject.toml`
- Create : `assistant/hermes/__init__.py` (vide)

**Interfaces :**
- Produces : `assistant.validation.validate_actions(actions, todoist) -> list[str]` (inchangée) ; `assistant.config.PluginConfig` et `load_config(env: Mapping[str, str], data_dir: Path) -> PluginConfig`.

- [ ] **Step 1 : Réécrire `tests/test_config.py`**

```python
def test_load_config_reads_token_users_and_db(tmp_path):
    c = load_config({"TODOIST_API_TOKEN": "tok", "TELEGRAM_ALLOWED_USERS": " 42 , 7 "}, tmp_path)
    assert c.todoist_token == "tok"
    assert c.allowed_user_ids == ("42", "7")
    assert c.owner_chat_id == "42"
    assert c.db_path == tmp_path / "assistant.db"
    assert c.timezone.key == "Europe/Paris"

def test_load_config_missing_values(tmp_path):
    with pytest.raises(ConfigError, match="TODOIST_API_TOKEN.*TELEGRAM_ALLOWED_USERS"):
        load_config({}, tmp_path)
```

- [ ] **Step 2 : Lancer le test et vérifier l'échec**

Run : `.venv/bin/pytest tests/test_config.py -q`
Expected : FAIL (`PluginConfig` / signature absente).

- [ ] **Step 3 : Implémenter `PluginConfig` (frozen dataclass : `todoist_token: str`, `allowed_user_ids: tuple[str, ...]`, `owner_chat_id: str`, `timezone: ZoneInfo`, `db_path: Path`) et `load_config(env, data_dir)` dans `assistant/config.py`**

`TIMEZONE` reste optionnelle (par défaut `Europe/Paris`), `owner_chat_id` vaut le premier ID autorisé et `ConfigError` est conservée.

- [ ] **Step 4 : Faire le reste du nettoyage listé dans Files, puis mettre à jour `pyproject.toml`**

Dépendances : `todoist-api-python` et `pydantic>=2`. Extras dev : `pytest`, `pytest-asyncio`, `python-telegram-bot>=22`, ce dernier pour les tests de la colle uniquement. Retirer `anthropic`, `groq`, `python-dotenv` et `pyyaml`.

- [ ] **Step 5 : Lancer toute la suite**

Run : `.venv/bin/pip install -e '.[dev]' -q && .venv/bin/pytest -q`
Expected : PASS (tests restants : config, models, render, executor, pending_store, todoist_client, validation via les tests existants).

- [ ] **Step 6 : Commit**

```bash
git add -A && git commit -m "refactor: retirer le bot autonome v1, préparer le plugin Hermes"
```

---

### Task 2 : Relais vers Hermes (réécritures, repli « ok », suivi des sessions)

**Files :**
- Create : `assistant/hermes/ports.py`, `assistant/hermes/relay.py`, `assistant/hermes/prompt.py`
- Test : `tests/test_relay.py`
- Modify : `tests/fakes.py` (ajout de `FakeInjector`)

**Interfaces :**
- Produces (`ports.py`) :
  - `Button` : réexport de `assistant.render.Button`.
  - `class Messenger(Protocol)` : `async def send(self, chat_id: str, text: str, buttons: list[Button] | None = None) -> int` (renvoie le `message_id`) ; `async def edit(self, chat_id: str, message_id: int, text: str, buttons: list[Button] | None = None) -> None`.
  - `class Injector(Protocol)` : `def inject(self, chat_id: str, text: str) -> bool`.
- Produces (`prompt.py`) :
  - `TASK_MARKER = "[Créer une tâche Todoist] "` ; `def as_task(text: str) -> str` (préfixe le marqueur).
  - `def edit_request(proposal_id: str, summary: str, correction: str) -> str` : texte demandant à Hermes de reproposer la version corrigée complète via `todoist_propose_actions` avec `replaces_proposal_id=<id>`.
  - `PROMPT_SECTION: str` : consignes §5.3 de la spec (marqueur ⇒ `todoist_propose_actions` ; Todoist seul endroit pour les tâches, rappels et suivis, sauf demande explicite contraire ; jamais de liste interne, de fichier ni de cron pour une tâche ; chercher avec `todoist_search_tasks` avant de modifier ; échéances `due_string` en anglais compris par Todoist ; ne jamais tenter d'écrire dans Todoist autrement).
- Produces (`relay.py`) : `class Relay(allowed_user_ids: tuple[str, ...], injector: Injector)` avec :
  - `note_admission(chat_id: str) -> None` / `note_reset(chat_id: str) -> None` / `has_human_turn(chat_id: str) -> bool`
  - `arm_ok(chat_id: str, text: str) -> None` : arme le repli (le texte est déjà marqué s'il s'agit d'une tâche).
  - `arm_edit(chat_id: str, proposal_id: str, summary: str) -> None`
  - `deliver(chat_id: str, text: str) -> bool` : injecte si `has_human_turn`, sinon arme le repli et renvoie `False`. Renvoie `True` seulement si l'injection a été acceptée.
  - `rewrite(user_id: str | None, chat_id: str, text: str) -> str | None` : `None` signifie « laisser passer tel quel ».

- [ ] **Step 1 : Écrire les tests**

```python
def test_slash_t_becomes_task(relay):
    assert relay.rewrite("42", "42", "/t acheter du pain") == "[Créer une tâche Todoist] acheter du pain"

def test_slash_t_without_text_is_untouched(relay):
    assert relay.rewrite("42", "42", "/t") is None

def test_ok_releases_armed_text_once(relay):
    relay.arm_ok("42", "[Créer une tâche Todoist] pain")
    assert relay.rewrite("42", "42", "  OK ") == "[Créer une tâche Todoist] pain"
    assert relay.rewrite("42", "42", "ok") is None

def test_ok_without_pending_is_untouched(relay):
    assert relay.rewrite("42", "42", "ok") is None

def test_edit_armed_rewrites_next_message_only(relay):
    relay.arm_edit("42", "p1", "📝 Créer : pain")
    out = relay.rewrite("42", "42", "plutôt vendredi")
    assert "p1" in out and "plutôt vendredi" in out and "replaces_proposal_id" in out
    assert relay.rewrite("42", "42", "autre chose") is None

def test_rewrite_ignores_unauthorized_user(relay):
    relay.arm_ok("42", "secret")
    assert relay.rewrite("99", "42", "ok") is None
    assert relay.rewrite("99", "99", "/t x") is None
    assert relay.rewrite("42", "42", "ok") == "secret"

def test_deliver_injects_only_after_human_turn(relay, injector):
    assert relay.deliver("42", "pain") is False          # session neuve : repli armé
    assert injector.calls == []
    assert relay.rewrite("42", "42", "ok") == "pain"
    relay.note_admission("42")
    assert relay.deliver("42", "lait") is True
    assert injector.calls == [("42", "lait")]

def test_reset_forgets_human_turn(relay, injector):
    relay.note_admission("42"); relay.note_reset("42")
    assert relay.deliver("42", "pain") is False

def test_refused_injection_arms_fallback(relay, injector):
    relay.note_admission("42"); injector.accept = False
    assert relay.deliver("42", "pain") is False
    assert relay.rewrite("42", "42", "ok") == "pain"
```

`FakeInjector` (dans `tests/fakes.py`) expose `calls: list[tuple[str, str]]` et `accept: bool = True`. Les fixtures `relay` et `injector` vont dans `tests/test_relay.py`, avec `allowed_user_ids=("42",)`.

- [ ] **Step 2 : Lancer les tests et vérifier l'échec**

Run : `.venv/bin/pytest tests/test_relay.py -q`
Expected : FAIL (module absent).

- [ ] **Step 3 : Implémenter `ports.py`, `prompt.py`, `relay.py` selon les interfaces**

L'état est gardé en mémoire, en dictionnaires par `chat_id`, protégés par un `threading.Lock`. Les hooks Hermes peuvent en effet être appelés depuis des threads.

- [ ] **Step 4 : Lancer les tests**

Run : `.venv/bin/pytest tests/test_relay.py -q`
Expected : PASS.

- [ ] **Step 5 : Commit**

```bash
git add assistant/hermes tests/test_relay.py tests/fakes.py
git commit -m "feat: relais vers Hermes (/t, repli ok, modification)"
```

---

### Task 3 : Parcours vocal (carte, boutons, confirmation de réception)

**Files :**
- Create : `assistant/hermes/voice.py`
- Test : `tests/test_voice.py`
- Modify : `tests/fakes.py` (ajout de `FakeMessenger`)

**Interfaces :**
- Consumes : `Messenger`, `Relay`, `as_task` (Tâche 2) ; `Clock` (`tests/conftest.py`).
- Produces : `class TranscriptionError(Exception)` ; `class VoiceFlow(messenger: Messenger, relay: Relay, transcribe: Callable[[bytes], str], clock: Callable[[], datetime], card_ttl=timedelta(hours=24), confirm_timeout=timedelta(seconds=180))` avec :
  - `async def on_voice(self, chat_id: str, audio: bytes) -> None`
  - `async def on_button(self, chat_id: str, message_id: int, verb: str, key: str) -> None`. Verbes : `send`, `task`, `cancel`, `again`.
  - `async def on_llm_call(self, user_message: str) -> None` : confirme la réception.
  - `async def tick(self) -> None` : expirations et délais de confirmation, appelé périodiquement par la colle.
  - `transcribe` est synchrone et lève `TranscriptionError`. `VoiceFlow` l'appelle via `asyncio.to_thread`.

Textes exacts :

| Situation | Texte de la carte | Boutons |
|---|---|---|
| Transcription OK | `🎙️ « {texte} »` | `💬 Discuter` `ta:v:send:<k>` · `📝 Créer une tâche` `ta:v:task:<k>` · `✖️ Annuler` `ta:v:cancel:<k>` |
| Texte vide | `Je n'ai pas compris, tu peux répéter ou l'écrire ?` | — |
| Échec de transcription | `⚠️ La transcription a échoué.` | `🔁 Réessayer` `ta:v:again:<k>` |
| Injecté, en attente | `🎙️ « {texte} »\n\n⏳ Transmis à Hermes…` | — |
| Confirmé | `🎙️ « {texte} »\n\n✅ Reçu par Hermes` (`📝 Reçu par Hermes comme tâche` si tâche) | — |
| Repli (session neuve) | `🎙️ « {texte} »\n\n👉 Réponds <b>ok</b> pour l'envoyer à Hermes.` | — |
| Délai dépassé | `🎙️ « {texte} »\n\n⚠️ Hermes n'a pas confirmé la réception. Réponds <b>ok</b> pour l'envoyer.` | — |
| Annulé | `🎙️ « {texte} »\n\n✖️ Annulé` | — |
| Clé inconnue ou expirée | nouveau message `Ce vocal a expiré ou a déjà été traité.` | — |

- [ ] **Step 1 : Écrire les tests** (asyncio, avec `FakeMessenger`, qui enregistre `sent: list[(chat_id, text, buttons)]` et `edits: list[(chat_id, message_id, text, buttons)]` et renvoie des `message_id` croissants)

```python
async def test_voice_shows_card_with_three_buttons(flow, messenger):
    await flow.on_voice("42", b"ogg")
    chat, text, buttons = messenger.sent[-1]
    assert text == "🎙️ « acheter du pain »"
    assert [b.data.split(":")[2] for b in buttons] == ["send", "task", "cancel"]
    assert all(b.data.startswith("ta:v:") for b in buttons)

async def test_voice_card_escapes_html(flow_with_text):
    flow, messenger = flow_with_text("a < b & c")
    await flow.on_voice("42", b"ogg")
    assert messenger.sent[-1][1] == "🎙️ « a &lt; b &amp; c »"

async def test_send_injects_and_waits_for_confirmation(flow, messenger, relay, injector):
    relay.note_admission("42"); await flow.on_voice("42", b"ogg"); key = _key(messenger)
    await flow.on_button("42", 1, "send", key)
    assert injector.calls == [("42", "acheter du pain")]
    assert messenger.edits[-1][2].endswith("⏳ Transmis à Hermes…")
    await flow.on_llm_call("blabla acheter du pain")
    assert messenger.edits[-1][2].endswith("✅ Reçu par Hermes")

async def test_task_button_marks_text(flow, relay, injector, messenger):
    relay.note_admission("42"); await flow.on_voice("42", b"ogg")
    await flow.on_button("42", 1, "task", _key(messenger))
    assert injector.calls == [("42", "[Créer une tâche Todoist] acheter du pain")]

async def test_double_send_injects_once(flow, relay, injector, messenger):
    relay.note_admission("42"); await flow.on_voice("42", b"ogg"); key = _key(messenger)
    await flow.on_button("42", 1, "send", key); await flow.on_button("42", 1, "send", key)
    assert len(injector.calls) == 1
    assert messenger.sent[-1][1] == "Ce vocal a expiré ou a déjà été traité."

async def test_new_session_uses_ok_fallback(flow, relay, injector, messenger):
    await flow.on_voice("42", b"ogg"); await flow.on_button("42", 1, "send", _key(messenger))
    assert injector.calls == []
    assert "Réponds <b>ok</b>" in messenger.edits[-1][2]
    assert relay.rewrite("42", "42", "ok") == "acheter du pain"

async def test_unconfirmed_after_timeout_arms_fallback(flow, relay, clock, messenger):
    relay.note_admission("42"); await flow.on_voice("42", b"ogg")
    await flow.on_button("42", 1, "send", _key(messenger))
    clock.advance(timedelta(seconds=181)); await flow.tick()
    assert "n'a pas confirmé" in messenger.edits[-1][2]
    assert relay.rewrite("42", "42", "ok") == "acheter du pain"

async def test_cancel_sends_nothing(flow, injector, messenger):
    await flow.on_voice("42", b"ogg"); await flow.on_button("42", 1, "cancel", _key(messenger))
    assert injector.calls == [] and messenger.edits[-1][2].endswith("✖️ Annulé")

async def test_card_expires_after_24h(flow, clock, injector, messenger):
    await flow.on_voice("42", b"ogg"); key = _key(messenger)
    clock.advance(timedelta(hours=24, seconds=1)); await flow.tick()
    await flow.on_button("42", 1, "send", key)
    assert injector.calls == []

async def test_transcription_failure_offers_retry_then_succeeds(flow_failing_once, messenger):
    flow = flow_failing_once
    await flow.on_voice("42", b"ogg")
    text, buttons = messenger.sent[-1][1:]
    assert text == "⚠️ La transcription a échoué." and buttons[0].data.startswith("ta:v:again:")
    await flow.on_button("42", 1, "again", buttons[0].data.split(":")[3])
    assert messenger.sent[-1][1] == "🎙️ « acheter du pain »"

async def test_empty_transcript(flow_with_text):
    flow, messenger = flow_with_text("  ")
    await flow.on_voice("42", b"ogg")
    assert messenger.sent[-1][1] == "Je n'ai pas compris, tu peux répéter ou l'écrire ?"
```

Fixtures locales à `tests/test_voice.py` : `flow` (transcription renvoyant « acheter du pain », `Relay(("42",), injector)`, horloge `clock`) ; `flow_with_text(t)` qui renvoie `(flow, messenger)` avec une transcription fixe ; `flow_failing_once` (lève `TranscriptionError` puis renvoie « acheter du pain ») ; `_key(messenger)` extrait la clé du premier bouton de la dernière carte envoyée.

- [ ] **Step 2 : Lancer les tests et vérifier l'échec**

Run : `.venv/bin/pytest tests/test_voice.py -q`
Expected : FAIL.

- [ ] **Step 3 : Implémenter `VoiceFlow`**

L'état est en mémoire. Une entrée par clé (`uuid4().hex[:10]`) contient le texte, le chat, le `message_id` de la carte, la date de création, l'état (`pending` / `waiting` / `done`) et l'indicateur « tâche ». Pour un échec de transcription, l'entrée garde l'audio jusqu'au succès du réessai. Le passage de `pending` à un autre état se fait sous verrou, pour qu'un double clic ne déclenche qu'un traitement. Pour la confirmation, `on_llm_call` cherche, parmi les entrées `waiting`, celle dont le texte injecté est contenu dans `user_message`.

- [ ] **Step 4 : Lancer les tests**

Run : `.venv/bin/pytest tests/test_voice.py -q`
Expected : PASS.

- [ ] **Step 5 : Commit**

```bash
git add assistant/hermes/voice.py tests/test_voice.py tests/fakes.py
git commit -m "feat: parcours vocal avec validation et confirmation de réception"
```

---

### Task 4 : Outils Todoist et cartes de proposition

**Files :**
- Create : `assistant/hermes/todoist_tools.py`
- Modify : `assistant/render.py` (préfixe des boutons), `tests/test_render.py`
- Test : `tests/test_todoist_tools.py`

**Interfaces :**
- Consumes : `Messenger`, `Relay.arm_edit` (Tâche 2) ; `PendingStore`, `Executor`, `validate_actions`, `render_proposal`, `render_results`, `format_tasks_for_llm`, `TodoistPort` (v1).
- Modifie : `proposal_buttons(proposal_id)` renvoie `ta:p:ok:<id>`, `ta:p:edit:<id>`, `ta:p:cancel:<id>` ; `retry_remaining_button` renvoie `ta:p:retry:<id>`. Le test existant de `tests/test_render.py` est mis à jour.
- Produces :
  - `TOOL_SCHEMAS: dict[str, dict]`, au format de schéma de fonction Hermes (`name`, `description`, `parameters`) pour : `todoist_list_projects` ({}), `todoist_search_tasks` (`query?`, `project?`, `due_filter?`), `todoist_get_task` (`task_id`), `todoist_propose_actions` (`actions`: tableau d'objets `{kind: create|update|complete|reopen|delete, task_id?, content?, due_string?, project?, priority?, labels?, description?}`, requis ; `replaces_proposal_id?`). Les descriptions de champs sont reprises de la v1 (`assistant/agent/tools.py`, supprimé à la Tâche 1 : `git show main:assistant/agent/tools.py`).
  - `def run_read_tool(name: str, args: dict, todoist: TodoistPort) -> str` (reprise de la v1).
  - `class ProposalFlow(store: PendingStore, executor: Executor, todoist: TodoistPort, messenger: Messenger, relay: Relay, owner_chat_id: str)` avec :
    - `async def propose(self, args: dict) -> str` : JSON. Succès : `{"status": "pending_user_validation", "proposal_id": ...}`. Erreur : `{"error": ...}`.
    - `async def on_button(self, chat_id: str, message_id: int, verb: str, proposal_id: str) -> None`. Verbes : `ok`, `retry`, `cancel`, `edit`.

- [ ] **Step 1 : Écrire les tests** (avec `FakeTodoist`, `store`, `FakeMessenger`, `relay` ; `Executor` appelé via `asyncio.to_thread`)

```python
async def test_propose_sends_card_and_writes_nothing(flow, todoist, messenger):
    out = json.loads(await flow.propose({"actions": [{"kind": "create", "content": "Pain"}]}))
    assert out["status"] == "pending_user_validation"
    assert todoist.calls["create_task"] == 0
    chat, text, buttons = messenger.sent[-1]
    assert chat == "42" and "Pain" in text
    assert [b.data for b in buttons] == [f"ta:p:{v}:{out['proposal_id']}" for v in ("ok", "edit", "cancel")]

async def test_propose_invalid_returns_error_without_card(flow, messenger):
    out = json.loads(await flow.propose({"actions": [{"kind": "complete"}]}))
    assert "error" in out and messenger.sent == []

async def test_propose_unknown_task_returns_validation_error(flow):
    out = json.loads(await flow.propose({"actions": [{"kind": "delete", "task_id": "nope"}]}))
    assert "introuvable" in out["error"]

async def test_multi_actions_single_card(flow, todoist, messenger):
    t = todoist.add("Lait")
    await flow.propose({"actions": [{"kind": "create", "content": "Pain"},
                                    {"kind": "complete", "task_id": t.id}]})
    assert len(messenger.sent) == 1

async def test_validate_executes_once(flow, todoist, messenger):
    pid = json.loads(await flow.propose({"actions": [{"kind": "create", "content": "Pain"}]}))["proposal_id"]
    await flow.on_button("42", 1, "ok", pid); await flow.on_button("42", 1, "ok", pid)
    assert todoist.calls["create_task"] == 1
    assert messenger.edits[0][2].startswith("✅ Fait")
    assert messenger.sent[-1][1] == "Cette proposition est expirée ou déjà traitée."

async def test_partial_offers_retry(flow, todoist, messenger):
    todoist.fail_on["create_task"] = TodoistError("boom"); todoist.fail_after["create_task"] = 1
    pid = json.loads(await flow.propose({"actions": [{"kind": "create", "content": "A"},
                                                     {"kind": "create", "content": "B"}]}))["proposal_id"]
    await flow.on_button("42", 1, "ok", pid)
    assert messenger.edits[-1][3][0].data == f"ta:p:retry:{pid}"

async def test_cancel(flow, todoist, messenger):
    pid = json.loads(await flow.propose({"actions": [{"kind": "create", "content": "Pain"}]}))["proposal_id"]
    await flow.on_button("42", 1, "cancel", pid)
    assert todoist.calls["create_task"] == 0 and messenger.edits[-1][2].startswith("❌ Annulé")

async def test_edit_arms_relay_and_replacement_closes_old_card(flow, relay, messenger, store):
    old = json.loads(await flow.propose({"actions": [{"kind": "create", "content": "Pain"}]}))["proposal_id"]
    await flow.on_button("42", 1, "edit", old)
    assert messenger.sent[-1][1] == "✏️ Que veux-tu changer ?"
    assert old in relay.rewrite("42", "42", "plutôt vendredi")
    await flow.propose({"actions": [{"kind": "create", "content": "Pain", "due_string": "friday"}],
                        "replaces_proposal_id": old})
    assert store.get(old).status == ProposalStatus.REPLACED
    assert any(e[1] == 1 and e[2].startswith("↪️ Remplacé") for e in messenger.edits)

async def test_click_on_replaced_proposal_refused(flow, todoist, messenger):
    old = json.loads(await flow.propose({"actions": [{"kind": "create", "content": "Pain"}]}))["proposal_id"]
    await flow.propose({"actions": [{"kind": "create", "content": "Pain 2"}], "replaces_proposal_id": old})
    await flow.on_button("42", 1, "ok", old)
    assert todoist.calls["create_task"] == 0
    assert messenger.sent[-1][1] == "Cette proposition est expirée ou déjà traitée."

def test_read_tools(todoist):
    todoist.add("Pain")
    assert "Pain" in run_read_tool("todoist_search_tasks", {"query": "pain"}, todoist)
    assert "introuvable" in run_read_tool("todoist_get_task", {"task_id": "x"}, todoist)
```

- [ ] **Step 2 : Lancer les tests et vérifier l'échec**

Run : `.venv/bin/pytest tests/test_todoist_tools.py tests/test_render.py -q`
Expected : FAIL.

- [ ] **Step 3 : Implémenter `todoist_tools.py` et adapter `render.py`**

- La carte (`render_proposal`) est envoyée à `owner_chat_id`, puis `store.set_message_id` est appelé.
- `replaces_proposal_id` effectue la transition `{PENDING}` → `REPLACED` et réédite l'ancienne carte avec `render_results`. Si l'ancienne carte n'est plus `PENDING`, le remplacement est ignoré et la nouvelle carte est envoyée quand même.
- Le message de refus est `Cette proposition est expirée ou déjà traitée.` : même texte que `EXPIRED_MSG` en v1, avec réédition `render_results` si le statut est `EXPIRED`.
- `edit` n'est accepté que si la proposition est `PENDING`. Il appelle alors `relay.arm_edit(chat_id, id, render_proposal(p))`.

- [ ] **Step 4 : Lancer toute la suite**

Run : `.venv/bin/pytest -q`
Expected : PASS.

- [ ] **Step 5 : Commit**

```bash
git add -A && git commit -m "feat: outils Todoist et cartes de proposition validées au clic"
```

---

### Task 5 : Colle Hermes, installation et remplacement de l'essai

**Files :**
- Create : `hermes_plugin/plugin.yaml`, `hermes_plugin/pyproject.toml`, `hermes_plugin/__init__.py`, `deploy/install_plugin.sh`
- Test : `tests/test_plugin_glue.py`

**Interfaces :**
- Consumes : `load_config`, `Relay`, `VoiceFlow`, `ProposalFlow`, `TOOL_SCHEMAS`, `run_read_tool`, `PROMPT_SECTION`, `TodoistClient`, `connect`, `PendingStore`, `Executor`.
- Produces : `hermes_plugin.parse_callback(data: str) -> tuple[str, str, str] | None` (`"ta:v:send:k"` → `("v", "send", "k")`, `None` hors préfixe ou mal formé) ; `register(ctx)`.

Câblage de `register(ctx)` (nom du plugin : `todoist-assistant`) :

| API Hermes | Branché sur |
|---|---|
| `sys.path.insert(0, <dossier du plugin>)` puis imports `assistant.*` | le paquet `assistant/` est copié à côté par le script d'installation |
| `load_config(os.environ, Path(os.environ["HERMES_HOME"]) / "plugin-data" / "todoist-assistant")` (repli `~/.hermes` si `HERMES_HOME` absent) | config |
| `ctx.register_platform_handler("telegram", _wire)` | `_wire(application, adapter)` mémorise `application.bot` et la boucle asyncio courante, et ajoute `MessageHandler(filters.VOICE \| filters.AUDIO, …)` (téléchargement en mémoire puis `VoiceFlow.on_voice`, uniquement si l'expéditeur est autorisé ; sinon `return` sans rien faire) et `CallbackQueryHandler(…, pattern=r"^ta:")` (`query.answer()`, puis `parse_callback`, puis `VoiceFlow.on_button` ou `ProposalFlow.on_button`, uniquement pour un utilisateur autorisé). Elle lance aussi une tâche asyncio qui appelle `VoiceFlow.tick()` toutes les 15 s. |
| `Messenger` réel | `bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(...))` (une rangée de boutons) / `bot.edit_message_text(...)` |
| `Injector` réel | `ctx.inject_message(text, role="user", session_key=f"agent:main:telegram:dm:{chat_id}")` |
| `transcribe` réel | fonction unique `_transcribe(audio: bytes) -> str` : fichier temporaire `.ogg`, `tools.transcription_tools.transcribe_audio(path, None, "gateway")`, `transcript` si `success`, sinon `TranscriptionError(error)` ; fichier toujours supprimé |
| `ctx.register_hook("pre_gateway_dispatch", …)` | `relay.rewrite(event.source.user_id, event.source.chat_id, event.text)` ⇒ `{"action": "rewrite", "text": …}` ou `None` ; seulement si `event.source.platform` vaut telegram |
| `ctx.register_hook("post_gateway_admission", …)` | `relay.note_admission(source["chat_id"])` pour telegram ; renvoie `None` (ne jamais renvoyer `handled`) |
| `ctx.register_hook("pre_command", …)` | si `command in {"new", "reset"}` et `session_key` telegram : `relay.note_reset(<chat_id extrait de la clé>)` |
| `ctx.register_hook("pre_llm_call", …)` | planifie `VoiceFlow.on_llm_call(user_message)` sur la boucle mémorisée (`asyncio.run_coroutine_threadsafe`) ; ne bloque pas |
| `ctx.register_tool(name, toolset="todoist", schema=…, handler=…)` ×4 | lectures : handler synchrone `run_read_tool`. `todoist_propose_actions` : `is_async=True`, `await proposal_flow.propose(args)`. Tous attrapent les exceptions et renvoient `{"error": ...}` en JSON |
| `ctx.register_system_prompt_section("todoist-assistant.rules", PROMPT_SECTION)` | consignes |

Les envois Telegram faits depuis les outils et les hooks passent par la boucle mémorisée dans `_wire`. Si `_wire` n'a pas encore été appelé (Telegram non connecté), `propose` renvoie `{"error": "Telegram n'est pas connecté : impossible d'envoyer la carte de validation."}`.

`hermes_plugin/pyproject.toml` contient `[project] name = "todoist-assistant"` et `dependencies = ["todoist-api-python", "pydantic>=2"]`.

`deploy/install_plugin.sh <hôte-ssh>` : rsync de `hermes_plugin/*` et de `assistant/` vers `/home/hermes/.hermes/plugins/todoist-assistant/` (en tant que `hermes`, via `sudo -u hermes` sur l'hôte), puis `hermes plugins enable todoist-assistant` (en acceptant la préparation des dépendances), `hermes config set plugins.entries.todoist-assistant.allow_gateway_injection true`, et `systemctl --user restart hermes-gateway` (`XDG_RUNTIME_DIR=/run/user/1001`).

- [ ] **Step 1 : Écrire les tests de la colle pure**

```python
@pytest.mark.parametrize("data,expected", [
    ("ta:v:send:abc", ("v", "send", "abc")), ("ta:p:ok:12ab", ("p", "ok", "12ab")),
    ("cl:1:2", None), ("ta:v:send", None), ("ta:x:ok:1", None)])
def test_parse_callback(data, expected):
    assert parse_callback(data) == expected

def test_register_wires_everything(fake_ctx, monkeypatch, tmp_path):
    monkeypatch.setenv("TODOIST_API_TOKEN", "t"); monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "42")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    register(fake_ctx)
    assert set(fake_ctx.tools) == {"todoist_list_projects", "todoist_search_tasks",
                                   "todoist_get_task", "todoist_propose_actions"}
    assert {"pre_gateway_dispatch", "post_gateway_admission", "pre_command", "pre_llm_call"} <= set(fake_ctx.hooks)
    assert fake_ctx.platform_handlers.keys() == {"telegram"}
    assert "todoist-assistant.rules" in fake_ctx.prompt_sections
```

`fake_ctx` est un petit objet de test qui enregistre les appels `register_*` et `inject_message`.

- [ ] **Step 2 : Lancer, vérifier l'échec, implémenter, relancer**

Run : `.venv/bin/pytest tests/test_plugin_glue.py -q`
Expected : FAIL, puis PASS après implémentation.

- [ ] **Step 3 : Déployer et retirer l'essai**

Sur le VPS (en tant que `hermes`) : `hermes plugins disable voice-confirm-spike`, puis `rm -rf ~/.hermes/plugins/voice-confirm-spike` et suppression de `plugins.entries.voice-confirm-spike` dans la config. Ajouter `TODOIST_API_TOKEN=` (saisi par l'utilisateur, jamais par Claude) dans `~/.hermes/.env`. Lancer ensuite `deploy/install_plugin.sh hermes-vps`.

Vérification : `journalctl --user -u hermes-gateway --since -2min` montre le chargement de `todoist-assistant` et « Connected to Telegram », sans erreur d'import. Un vocal fait apparaître la carte à 3 boutons.

- [ ] **Step 4 : Commit**

```bash
git add hermes_plugin deploy/install_plugin.sh tests/test_plugin_glue.py
git commit -m "feat: plugin Hermes todoist-assistant et script d'installation"
```

---

### Task 6 : Isoler les outils de l'agent dans Docker rootless

Tâche d'exploitation sur le VPS. Les commandes root passent par `ssh hermes-vps` (compte `claude`), celles de l'utilisateur par `sudo -iu hermes`.

**Files :**
- Create : `deploy/sandbox.md` (commandes exactes exécutées et résultat des vérifications)

- [ ] **Step 1 : Installer Docker et les outils rootless (root)**

Dépôt officiel Docker pour Ubuntu, paquets `docker-ce docker-ce-cli docker-ce-rootless-extras uidmap`, puis `systemctl disable --now docker.service docker.socket` : pas de démon root. Vérifier que `hermes` a des plages dans `/etc/subuid` et `/etc/subgid`, sinon les ajouter.

- [ ] **Step 2 : Docker rootless pour `hermes`**

`dockerd-rootless-setuptool.sh install`, puis `systemctl --user enable --now docker`. Ajouter `DOCKER_HOST=unix:///run/user/1001/docker.sock` à l'environnement de `hermes-gateway`, par `~/.hermes/.env` ou un drop-in du service utilisateur.
Vérification : `docker run --rm hello-world` réussit en tant que `hermes`, et `id hermes` ne montre **pas** le groupe `docker`. Si le mode rootless échoue, **s'arrêter et demander à l'utilisateur** (contrainte de la spec §6.2).

- [ ] **Step 3 : Basculer Hermes**

`hermes config set terminal.backend docker`. Garder `terminal.docker_mount_cwd_to_workspace false`. Vérifier dans la config qu'aucune variable `TODOIST_*` n'est transmise au conteneur. Redémarrer la gateway.

- [ ] **Step 4 : Vérifier l'isolation** (via `hermes chat -q … --oneshot -Q`, puis sur Telegram)

- `cat /home/hermes/.hermes/.env` ⇒ échec (fichier absent du conteneur).
- Outil de lecture de fichier sur `/home/hermes/.hermes/.env` ⇒ échec.
- `env | grep -i todoist` ⇒ vide.
- `search_files` dans `/home/hermes` ⇒ ne voit pas `.hermes`.
- `hermes tools` : lister les outils actifs et confirmer qu'aucun outil restant sur l'hôte ne permet de lire des fichiers ou de lancer des commandes. Si l'un d'eux le permet, le désactiver ou le signaler à l'utilisateur.

Consigner chaque commande et chaque résultat dans `deploy/sandbox.md`.

- [ ] **Step 5 : Commit**

```bash
git add deploy/sandbox.md && git commit -m "ops: outils de l'agent Hermes isolés dans Docker rootless"
```

---

### Task 7 : README et recette complète

**Files :**
- Modify : `README.md` (réécrit pour le plugin : clés `TODOIST_API_TOKEN` et `GROQ_API_KEY` dans `~/.hermes/.env`, installation via `deploy/install_plugin.sh`, isolation Docker → `deploy/sandbox.md`, recette ci-dessous, et la recette courte à relancer après chaque `hermes update` : vocal → carte, `/t` → carte Todoist, Valider)

- [ ] **Step 1 : Recette sur le VPS avec le projet Todoist « Bot-test »**, en cochant chaque point dans le README

1. Vocal → carte à 3 boutons → **Discuter** → « ⏳ Transmis » puis « ✅ Reçu par Hermes » et réponse de Hermes.
2. Vocal « ajoute acheter du pain demain dans Bot-test » → **Créer une tâche** → carte Todoist → **Valider** → tâche visible dans Todoist.
3. Vocal → **Annuler** → Hermes ne reçoit rien.
4. `/t acheter du lait vendredi` → carte Todoist.
5. `/new` puis vocal → **Discuter** → « Réponds ok » → `ok` → Hermes répond à la transcription.
6. « le pain c'est fait » → carte **Terminer** → Valider.
7. Carte → **Modifier** → « plutôt samedi » → l'ancienne carte passe à « ↪️ Remplacé » et une nouvelle arrive.
8. Double clic sur **Valider** → une seule tâche créée.
9. « rappelle-moi d'appeler le plombier lundi » (sans bouton) → carte Todoist, sans cron ni liste interne : vérifier `hermes cron list` et l'absence de fichier créé.
10. Demander à Hermes d'écrire lui-même dans Todoist via le terminal ou `curl` → impossible (clé introuvable).
11. Bouton natif de Hermes (question `clarify` ou approbation) → fonctionne toujours.
12. Message d'un autre compte Telegram → aucune réaction.
13. `systemctl --user restart hermes-gateway`, puis clic sur une carte Todoist envoyée avant → fonctionne. Clic sur une carte vocale d'avant → « expiré ou déjà traité ».

- [ ] **Step 2 : Commit**

```bash
git add README.md && git commit -m "docs: installation et recette du plugin Hermes"
```
