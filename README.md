# Assistant de tâches (plugin Hermes)

Plugin Telegram pour [Hermes](https://github.com/NousResearch/hermes-agent) : tu envoies un vocal, une carte à boutons apparaît (💬 Discuter · 📝 Créer une tâche · ✖️ Annuler). Pour une tâche, Hermes propose les actions Todoist correspondantes sous forme de **cartes** ; rien n'est écrit dans Todoist avant ton clic sur **Valider**.

Spec : `docs/superpowers/specs/2026-11-10-plugin-hermes-taches-design.md`

## Fonctionnement

- **Vocal** : transcrit (Groq), puis carte à trois boutons. **Discuter** transmet la transcription à Hermes (« ⏳ Transmis à Hermes… » puis « ✅ Reçu par Hermes » ; si la réception n'est pas confirmée en 180 s, la carte te demande de répondre `ok`). **Créer une tâche** transmet le texte précédé du marqueur `[Créer une tâche Todoist] `. **Annuler** : Hermes ne reçoit rien. Une carte vocale expire après 24 h.
- **`/t <texte>`** : demande de tâche directe, sans passer par un vocal.
- **Cartes Todoist** (créer, terminer, modifier…) : **Valider**, **Modifier** (tu corriges en une phrase ; l'ancienne carte passe à « ↪️ Remplacé »), **Annuler**. Seul un clic sur **Valider** (ou « Réessayer ce qui reste ») écrit dans Todoist.
- Seuls les comptes de `TELEGRAM_ALLOWED_USERS` sont écoutés ; les cartes sont envoyées en message privé au premier ID de la liste.

## Clés et réglages

| Où | Variable / fichier | Origine |
|---|---|---|
| `~/.hermes/.env` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS` | Déjà présents (Hermes). ID numérique via **@userinfobot**. |
| `~/.hermes/.env` | `GROQ_API_KEY` | console.groq.com → API Keys (transcription). |
| `/home/hermes/.config/todoist-assistant/token` | jeton d'API Todoist | Todoist → Paramètres → Intégrations → Développeur. Dossier 0700, fichier 0600, propriétaire `hermes`. |

**Le jeton Todoist ne va jamais dans `~/.hermes/.env`** (aucune variable `TODOIST_*` dans l'environnement de Hermes) et n'est jamais donné à Hermes : seul le plugin le lit, dans ce fichier.

## Installation sur le VPS

Depuis le poste de dev (le compte SSH doit avoir sudo sans mot de passe ; les fichiers sont écrits en tant que `hermes`) :

```bash
deploy/install_plugin.sh <hôte-ssh>
```

Le script copie le plugin dans `~hermes/.hermes/plugins/todoist-assistant`, l'active (`hermes plugins enable`), autorise l'injection côté gateway et redémarre `hermes-gateway`. Préalables : Hermes installé pour l'utilisateur `hermes`, `rsync` des deux côtés, jeton Todoist posé dans le fichier ci-dessus. Vérifier : `journalctl --user -u hermes-gateway --since -2min`.

## Isolation (à lire)

Les outils de l'agent (terminal, fichiers, code) tournent dans un conteneur Docker rootless qui ne voit ni `.env`, ni le jeton Todoist. Mise en place, vérifications et **liste de contrôle après chaque `hermes update`** : `deploy/sandbox.md`. Règles :

- **Ne jamais approuver** un skill qui demande des variables `TODOIST_*` ou des fichiers d'identifiants ; ne jamais donner la clé Todoist à Hermes.
- Hermes n'a **pas d'outil navigateur** (désactivé volontairement, il s'exécuterait sur l'hôte), ni `connections`.
- `skills.write_approval` est activé : toute écriture de skill demande ton approbation (`/skills pending`).

## Développement

Python ≥ 3.11 :

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' && .venv/bin/pytest -q
```

## Recette complète (avant usage réel)

Projet Todoist **Bot-test**, sur le VPS ; cocher chaque point.

- [ ] 1. Vocal → carte à 3 boutons → **💬 Discuter** → « ⏳ Transmis à Hermes… » puis « ✅ Reçu par Hermes », et réponse de Hermes.
- [ ] 2. Vocal « ajoute acheter du pain demain dans Bot-test » → **📝 Créer une tâche** → carte Todoist → **Valider** → tâche visible dans Todoist.
- [ ] 3. Vocal → **✖️ Annuler** → Hermes ne reçoit rien.
- [ ] 4. `/t acheter du lait vendredi` → carte Todoist.
- [ ] 5. `/new` puis vocal → **💬 Discuter** → « Réponds ok » → `ok` → Hermes répond à la transcription.
- [ ] 6. « le pain c'est fait » → carte **Terminer** → Valider.
- [ ] 7. Carte → **Modifier** → « plutôt samedi » → l'ancienne carte passe à « ↪️ Remplacé » et une nouvelle arrive.
- [ ] 8. Double clic sur **Valider** → une seule tâche créée.
- [ ] 9. « rappelle-moi d'appeler le plombier lundi » (sans bouton) → carte Todoist, sans cron ni liste interne : vérifier `hermes cron list` et l'absence de fichier créé.
- [ ] 10. Demander à Hermes d'écrire lui-même dans Todoist via le terminal ou `curl` → impossible (clé introuvable).
- [ ] 11. Bouton natif de Hermes (question `clarify` ou approbation) → fonctionne toujours.
- [ ] 12. Message d'un autre compte Telegram → aucune réaction.
- [ ] 13. `systemctl --user restart hermes-gateway`, puis clic sur une carte Todoist envoyée avant → fonctionne. Clic sur une carte vocale d'avant → « expiré ou déjà traité ».

## Recette courte après chaque `hermes update`

1. Contrôles de sécurité de `deploy/sandbox.md` (section « Après chaque `hermes update` »).
2. Vocal → la carte à 3 boutons apparaît.
3. `/t acheter du lait vendredi` → carte Todoist.
4. **Valider** → tâche créée dans Todoist (puis la supprimer).
