# Assistant vocal de tâches

Bot Telegram personnel : tu envoies un vocal (ou un texte), Claude propose les actions Todoist correspondantes, tu valides d'un clic.

Spec : `docs/superpowers/specs/2026-11-09-assistant-vocal-taches-design.md`

## 1. Récupérer les clés

| Variable | Où l'obtenir |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Dans Telegram, écrire à **@BotFather** → `/newbot` → copier le jeton. |
| `TELEGRAM_ALLOWED_USER_ID` | Écrire à **@userinfobot** : il répond avec ton identifiant numérique. |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys. **Fixe un plafond de dépense mensuel** (Settings → Limits), par ex. 5 $. |
| `TODOIST_API_TOKEN` | Todoist → Paramètres → Intégrations → Développeur → jeton d'API. **Plugin Hermes :** jamais dans `~/.hermes/.env` ; le mettre dans `~/.config/todoist-assistant/token` (dossier 0700, fichier 0600) de l'utilisateur `hermes`, voir `deploy/sandbox.md`. |
| `GROQ_API_KEY` | console.groq.com → API Keys. |

## 2. Installer sur le VPS

```bash
sudo useradd --system --home /opt/assistant-taches --shell /usr/sbin/nologin assistant
sudo git clone <ce dépôt> /opt/assistant-taches     # ou copier le dossier
cd /opt/assistant-taches
sudo python3 -m venv .venv
sudo .venv/bin/pip install .
sudo cp .env.example .env && sudo nano .env          # remplir les clés
sudo mkdir -p data && sudo chown -R assistant:assistant /opt/assistant-taches
sudo chmod 600 .env
sudo cp deploy/assistant.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now assistant
journalctl -u assistant -f                            # suivre les journaux
```

Python ≥ 3.11 requis. Aucun port à ouvrir : le bot va chercher les messages chez Telegram (long polling).

Mise à jour : `git pull && .venv/bin/pip install . && sudo systemctl restart assistant`.

## 3. Développement

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

Évaluation de la compréhension (appelle le vrai Claude, quelques centimes) :

```bash
ANTHROPIC_API_KEY=... .venv/bin/python evals/run_evals.py
```

## 4. Recette manuelle (avant usage réel)

Crée un projet Todoist **Bot-test**, puis vérifie :

1. Vocal « ajoute acheter du pain demain dans Bot-test » → transcription affichée → proposition → **Valider** → tâche visible dans Todoist.
2. « Le pain c'est fait » → proposition **Terminer** → Valider.
3. Une proposition → **Modifier** → « plutôt vendredi » → l'ancien message passe à « Remplacé », une nouvelle proposition arrive.
4. Une proposition → **Annuler**.
5. Double-clic rapide sur **Valider** → une seule tâche créée.
6. « Qu'est-ce que j'ai aujourd'hui ? » → réponse sans bouton.
7. Message depuis un autre compte Telegram → aucune réponse.
8. `sudo systemctl restart assistant`, puis clic sur un bouton d'une proposition envoyée avant → fonctionne.
