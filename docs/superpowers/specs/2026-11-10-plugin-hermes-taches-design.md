# Assistant de tâches — passage par Hermes (plugin Telegram + Todoist)

Date : 2026-11-10
Statut : en relecture
Remplace, pour le déploiement, le bot autonome décrit dans `2026-11-09-assistant-vocal-taches-design.md` (ci-après « spec v1 »). Les règles métier de la spec v1 (statuts, gestion des erreurs, validation) restent valables sauf mention contraire.

## 1. Intention

L'utilisateur veut **un seul assistant sur Telegram : Hermes** (Hermes Agent, Nous Research, déjà installé sur son VPS). Il lui parle de tout, pas seulement de ses tâches, et Hermes **garde le contexte** d'une conversation à l'autre (mémoire persistante). Ce contexte doit servir aussi pour les tâches Todoist : si l'utilisateur a dit « je suis en vacances du 20 au 27 », une tâche « appeler le plombier la semaine prochaine » doit pouvoir en tenir compte.

Le bot taskManager n'est donc **pas déployé comme bot séparé**. Ses capacités Todoist deviennent un **plugin Hermes**.

### Objectifs
- Les vocaux sont transcrits et **l'utilisateur valide la transcription** avant que quoi que ce soit parte à Hermes.
- L'utilisateur indique explicitement quand il veut **créer une tâche** (bouton sur le vocal, `/t` pour le texte).
- **Aucune écriture dans Todoist sans clic de validation**, et cette règle est **imposée par le code**, pas par une consigne au modèle.
- Hermes peut lire Todoist librement (agenda, recherche) et proposer toute action (créer, modifier, terminer, rouvrir, supprimer).

### Non-objectifs
- Garder le bot taskManager autonome en parallèle.
- Réponses vocales.
- Gmail, Calendar, briefs programmés (sous-projets ultérieurs de la spec v1).
- Faire de la création de tâche en « un seul clic » : l'utilisateur a accepté **deux clics** pour un vocal de tâche (« Créer une tâche » puis « Valider »), le second servant à vérifier ce que Hermes a déduit (date, projet).

### Anti-objectifs (échec même si « ça marche »)
- Une écriture Todoist qui a lieu sans clic de l'utilisateur, par n'importe quel chemin (outil, terminal de l'agent, script).
- Un vocal qui part à Hermes sans validation.
- Un message affiché « ✅ Envoyé » alors que Hermes n'a rien reçu (constaté pendant l'essai, voir §6).
- Hermes qui range une tâche ailleurs que dans Todoist (sa liste interne, un fichier, un rappel cron) quand l'utilisateur a demandé une tâche.

## 2. État existant

- **VPS** : Ubuntu 26.04, utilisateur système `hermes` **sans sudo**, Hermes v0.23.1 installé dans `/home/hermes/.hermes/hermes-agent` (méthode git, canal stable).
- **Gateway** Hermes : service systemd utilisateur `hermes-gateway` (linger activé, démarrage au boot), Telegram en long polling, un seul utilisateur autorisé (`TELEGRAM_ALLOWED_USERS` dans `~/.hermes/.env`).
- **Modèle** : `claude-haiku-5-5` via clé API Anthropic.
- **Transcription** : intégrée à Hermes, `stt.provider: groq`, `whisper-large-v3-turbo`, langue `fr`, clé `GROQ_API_KEY` dans `~/.hermes/.env`.
- **Essai jetable** : plugin `~/.hermes/plugins/voice-confirm-spike/` (à supprimer une fois le vrai plugin en place).
- **Dépôt taskManager** : modules réutilisables `todoist_client`, `models`, `pending_store`, `executor`, `render`, `agent/validation`, `db`, avec leurs tests et faux (`tests/fakes.py`).

## 3. Approches envisagées

| | Approche | Compromis |
|---|---|---|
| A | Hermes seul, validation demandée par consigne (`SOUL.md`) et outil `clarify` ; Todoist via un connecteur MCP | Presque rien à coder, mais la validation dépend du bon vouloir du modèle : contraire à l'anti-objectif n°1. |
| **B — retenue** | **Hermes + plugin garde-fou** : le plugin intercepte les vocaux, affiche ses propres boutons, injecte le texte validé ; il fournit les outils Todoist et n'écrit qu'au clic | Validation garantie par le code, mémoire Hermes complète, réutilise le code taskManager. Dépend de l'API de plugins de Hermes, qui évolue vite. |
| C | Bot taskManager en façade, Hermes derrière via son API | Parcours actuel intact, mais deux systèmes et perte des fonctions Telegram natives de Hermes. |

L'utilisateur a choisi **B**.

## 4. Ce que l'essai a établi (2026-11-10)

Plugin jetable testé sur le vrai Telegram :

| Point | Résultat |
|---|---|
| `ctx.register_platform_handler("telegram", factory)` donne l'`Application` python-telegram-bot ; un `MessageHandler(filters.VOICE)` du plugin passe **avant** Hermes | ✅ |
| `tools.transcription_tools.transcribe_audio(path, None, "gateway")` réutilise la config STT de Hermes ; retour `{"success", "transcript", "provider"}` ; ~2–3 s | ✅ |
| Boutons inline du plugin et `CallbackQueryHandler(pattern=r"^<prefixe>:")` | ✅ |
| `ctx.inject_message(texte, role="user", session_key="agent:main:telegram:dm:<chat_id>")` (permission `plugins.entries.<id>.allow_gateway_injection: true`) | ✅ quand la session contient déjà un message humain |
| Même injection quand le vocal est le **premier message** de la session (après `/new`) | ❌ `inject_message` renvoie `True`, puis la gateway refuse (`not_found`) et rien n'arrive à Hermes |
| Sur « rappelle-moi d'acheter du pain », Hermes (sans outil Todoist) a fouillé les fichiers (`search_files`) | ⚠️ confirme le besoin de consignes |

Points d'API documentés (non testés) à utiliser : hook `pre_gateway_dispatch` (retour `{"action": "skip"}` / `{"action": "rewrite", "text"}` ; s'exécute sur chaque message entrant **avant** l'authentification et **avant** la transcription), `ctx.register_tool`, `ctx.register_skill`, `ctx.register_command`.

## 5. Parcours

### 5.1 Vocal
1. Le plugin intercepte le vocal (utilisateur autorisé uniquement ; sinon il laisse Hermes l'ignorer ou l'ignore lui-même).
2. Transcription via la fonction de Hermes. Échec ou texte vide : mêmes messages que la spec v1 (§5), avec `[🔁 Réessayer]`.
3. Carte : `🎙️ « … »` + **[💬 Discuter] [📝 Créer une tâche] [✖️ Annuler]**.
4. *Discuter* : le texte est transmis à Hermes tel quel, **immédiatement et sans autre validation** (y compris en session neuve après `/new` ou après un redémarrage de la gateway). *Créer une tâche* : il est transmis **marqué comme demande de création de tâche** (§5.3). *Annuler* : rien ne part. Transmission : le plugin remet au cœur Telegram de Hermes un message **texte** synthétique de l'utilisateur (le vocal d'origine, sans le média, avec la transcription pour texte) ; Hermes le traite comme un vrai message tapé (§6.1).
5. La carte est éditée avec l'état réel (« ⏳ Transmis à Hermes… » puis « ✅ Reçu par Hermes » / « 📝 Reçu par Hermes comme tâche », ou « ✖️ Annulé »). Sans confirmation de réception en 180 s : « ⚠️ Hermes n'a pas confirmé la réception. » avec **[🔁 Renvoyer]** (un seul renvoi). Un double clic ne transmet qu'une fois. Si une correction *Modifier* (§5.4) est attendue, la transcription validée sert de correction. Une carte non traitée expire (durée au choix du constructeur, ~24 h comme la spec v1).

### 5.2 Texte
- `/t <texte>` : équivaut à « Créer une tâche » pour un message tapé. Implémentation recommandée : réécriture via `pre_gateway_dispatch` (le message reste un vrai message humain, donc pas de problème de session).
- Tout autre texte : va à Hermes sans intervention du plugin (sauf mode *Modifier*, §5.4).

### 5.3 Marquage « tâche »
Le texte transmis porte un marqueur explicite (forme au choix du constructeur, ex. `[Créer une tâche Todoist] acheter du pain demain`). Une consigne fournie par le plugin (skill et/ou section de prompt) dit à Hermes :
- un message marqué ⇒ appeler `todoist_propose_create_task` (avec contexte : date du jour, projets, ce qu'il sait de l'utilisateur) ;
- pour toute demande de tâche, rappel ou suivi, **Todoist est le seul endroit** (pas de liste interne, fichier ni cron), sauf demande explicite contraire de l'utilisateur ;
- les modifications / clôtures (« le pain c'est fait ») passent par les outils `propose_*` correspondants.

### 5.4 Écritures Todoist (carte de proposition)
Repris de la spec v1 (§4 points 3–5, statuts, erreurs) avec ces changements :
- Les outils `todoist_propose_*` (create, update, complete, reopen, delete) **n'écrivent rien** : ils valident les actions (`validation.validate_actions`), créent la proposition dans `pending_store`, **envoient eux-mêmes la carte** `[✅ Valider] [✏️ Modifier] [❌ Annuler]` au chat Telegram, et renvoient à Hermes un résultat du type « proposition envoyée, en attente de validation de l'utilisateur ». En cas d'action invalide, ils renvoient l'erreur à Hermes pour correction (Hermes gère ses propres tentatives).
- Plusieurs actions d'une même demande doivent pouvoir former **une seule** proposition (comme en v1). Forme d'outil au choix du constructeur (un outil multi-actions ou regroupement par tour).
- *Valider* : exécution par `executor`, édition de la carte avec le résultat (✅ / détail par action / `partial` + « Réessayer ce qui reste »). Seul ce clic écrit dans Todoist.
- *Modifier* : comme en v1, le message suivant de l'utilisateur est la correction ; le plugin le réécrit (`pre_gateway_dispatch`) en une demande à Hermes contenant la proposition d'origine, et la nouvelle proposition remplace l'ancienne (`replaced`).
- Hermes n'est pas relancé après exécution (pas de nouveau tour ni de réponse supplémentaire) ; il peut relire Todoist s'il a besoin de l'état.
- Lectures (`todoist_list_projects`, `todoist_search_tasks`, `todoist_get_task`) : libres, sans carte.

## 6. Contraintes et points durs

1. **Injection refusée en début de session.** `inject_message` n'aboutit que si la session Hermes contient déjà un message humain validé (et le suivi de ces tours, en mémoire, se perd à chaque redémarrage). Le repli « réponds *ok* » d'abord retenu est **abandonné** à la demande de l'utilisateur (« Discuter » doit envoyer directement). Solution retenue : le plugin n'injecte plus ; il soumet à l'application Telegram de Hermes (`application.process_update`) un update **texte** synthétique construit à partir du vocal d'origine (même chat, même auteur, même `message_id`, média retiré, `text` = transcription), avec un `update_id` négatif unique pour ne pas être écarté par la déduplication des updates de Hermes (reçus d'admission) ni toucher à l'offset de polling. Le cœur le traite comme un message tapé (tour humain réel). Le hook `pre_gateway_dispatch` ne l'interprète jamais comme `/t` ; un vocal brut du propriétaire y est écarté (`skip`) en second garde-fou.
2. **La clé Todoist ne doit pas être accessible à l'agent.** Aujourd'hui `terminal.backend: local` : les outils de l'agent s'exécutent sous l'utilisateur `hermes`, et tout ce que le plugin peut lire, l'agent le peut aussi. **Décision de l'utilisateur : passer les outils de Hermes dans un conteneur Docker** (`terminal.backend: docker`). D'après la doc Hermes, le conteneur sert au `terminal`, à `execute_code` et aux outils de fichiers. À faire :
   - installer Docker (root, via le compte `claude`) et donner à `hermes` le droit de l'utiliser **en mode rootless** de préférence (le groupe `docker` équivaut à root sur l'hôte, ce qui annulerait le retrait du sudo) — si le mode rootless pose problème, revenir vers l'utilisateur avant d'utiliser le groupe `docker` ;
   - n'exposer dans le conteneur ni `~/.hermes` (`.env`, base du plugin, config), ni la clé Todoist (`TODOIST_API_TOKEN` hors des variables transmises au conteneur) ; garder `docker_mount_cwd_to_workspace: false` ;
   - vérifier que **tous** les outils de l'agent qui touchent au système (y compris `search_files` et d'éventuels outils non listés dans la doc) passent par le conteneur ; tout outil restant sur l'hôte et capable de lire des fichiers ou lancer des commandes doit être désactivé ou signalé à l'utilisateur ;
   - conséquence acceptée : Hermes ne voit plus les fichiers du VPS hors de son espace de travail ; sa mémoire, ses skills et la gateway ne changent pas.
   La recette (§9) inclut une tentative explicite de lecture de la clé et d'appel Todoist depuis l'agent.
3. **Dépendance à l'API de plugins Hermes.** Version de référence : v0.23.1. Hermes se met à jour souvent (`hermes update`) : le plugin doit rester simple, se cantonner aux API documentées (`register_platform_handler`, `inject_message`, `pre_gateway_dispatch`, `register_tool`, `register_skill`, `register_command`) et s'accompagner d'une recette rapide à relancer après chaque mise à jour. L'appel à `tools.transcription_tools.transcribe_audio` est une API interne : l'isoler derrière une petite fonction.
4. **Préfixe des callbacks** Telegram unique au plugin (ex. `ta:`), sinon le gestionnaire avalerait les boutons natifs de Hermes (approbations, `clarify`, choix du modèle).
5. **Un seul utilisateur** : le plugin filtre lui-même sur `TELEGRAM_ALLOWED_USERS` pour ce qu'il traite avant Hermes (vocaux, clics).
6. **Pas de sudo pour `hermes`** : toute installation système (paquet, Docker) passe par l'utilisateur ou par Claude via le compte `claude`, qui a sudo sans mot de passe.
7. **Aucun accès Todoist existant côté Hermes** (vérifié le 2026-11-10 : pas de `TODOIST_*` dans `~/.hermes/.env`, pas de serveur MCP, pas de skill Todoist). Le plugin doit rester le seul chemin vers Todoist ; la recette le revérifie.
8. **Dépendances** : Hermes gère un environnement Python partagé via son gestionnaire de paquets (PM), qui prépare les dépendances de Hermes et des plugins activés ensemble (consentement explicite à l'installation). L'environnement de la gateway contient déjà `python-telegram-bot` 22.8 et `pydantic` 2.14 : le plugin s'aligne dessus (pas d'épinglage contraire) et déclare `todoist-api-python` selon le mécanisme « lazy-install optional Python dependencies » du guide de plugins.
9. **Routage des cartes** *(choix de Claude)* : les cartes de proposition sont toujours envoyées en message privé à l'utilisateur unique (`TELEGRAM_ALLOWED_USERS`), quelle que soit la session d'où l'outil est appelé (Telegram, CLI, tâche cron). Si l'envoi Telegram échoue, l'outil renvoie l'erreur à Hermes.

## 7. Ce qui est réutilisé / retiré du dépôt

- **Réutilisé** : `todoist_client` (+ `TodoistPort`, faux Todoist), `models`, `pending_store`, `executor`, `render` (adapté au préfixe de callback), `agent/validation`, `db`, `transcriber` uniquement si le constructeur préfère ne pas dépendre de la fonction interne de Hermes.
- **Retiré du chemin de déploiement** : `agent/agent.py` (boucle Claude), `bot.py`, `main.py`, `deploy/assistant.service`, `ANTHROPIC_API_KEY` du projet. Le constructeur décide s'il les supprime du dépôt ou les laisse.
- `controller.py` : sa logique de clic (transitions, double clic, expiration, réessai) est à reprendre dans le plugin.
- `evals/` : à adapter si le constructeur juge utile d'évaluer la compréhension via Hermes ; sinon hors périmètre.

## 8. Déploiement

- Le plugin vit dans le dépôt (emplacement au choix du constructeur) et est installé dans `~/.hermes/plugins/<nom>/` de l'utilisateur `hermes`, avec ses dépendances Python (`todoist-api-python`, `pydantic`) préparées selon le mécanisme de dépendances des plugins Hermes.
- Activation : `hermes plugins enable <nom>`, redémarrage de `hermes-gateway`.
- Base SQLite des propositions dans un dossier de données du plugin, persistant aux redémarrages.
- Supprimer et désactiver `voice-confirm-spike`.
- Mise à jour du README : installation du plugin, clés, recette.

## 9. Tests

- TDD pytest, en reprenant les tests existants des modules réutilisés.
- Le cœur du plugin (cartes, transitions, réécritures, marquage) doit être testable **sans Hermes ni Telegram**, avec des faux pour la transcription, la soumission à Hermes et l'envoi Telegram, comme `controller` l'était en v1.
- Recette manuelle sur le VPS (projet Todoist « Bot-test ») : la liste de la spec v1 §4 du README, plus : vocal → Envoyer ; vocal → Créer une tâche → Valider ; vocal → Annuler ; `/t` ; vocal comme premier message après `/new` (envoi direct) ; « le pain c'est fait » → carte Terminer ; demande de tâche ⇒ jamais de liste interne ni de cron ; tentative de faire écrire Todoist par l'agent via le terminal ⇒ impossible (selon la solution du §6.2) ; boutons natifs de Hermes toujours fonctionnels.

## 10. Laissé au constructeur

Forme exacte du marqueur « tâche », découpage des outils `propose_*`, emplacement du plugin dans le dépôt, durée d'expiration des cartes vocales, choix de détection du cas « premier message », détails de mise en œuvre du conteneur (§6.2), sort des modules retirés.
