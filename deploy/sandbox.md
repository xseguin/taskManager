# Outils de l'agent Hermes isolés dans Docker rootless

Mise en œuvre de la spec §6.2 (la clé Todoist ne doit pas être accessible à l'agent), le 2026-11-10.
VPS : Ubuntu 26.04 (`resolute`), x86_64. Hermes v0.23.1, utilisateur `hermes` (uid 1001, sans sudo).
Les commandes root passent par `ssh hermes-vps` (compte `claude`), celles de l'utilisateur par
`sudo -iu hermes`.

Résultat : `terminal`, `execute_code` et les outils de fichiers (`read_file`, `write_file`, `patch`,
`search_files`) s'exécutent dans un conteneur Docker lancé par le démon rootless de `hermes`. Le
conteneur ne voit ni `~/.hermes/.env`, ni `config.yaml`, ni les données du plugin, ni aucune variable
`TODOIST_*`. La gateway (hors conteneur) garde la clé et le plugin `todoist-assistant` fonctionne.

## 1. Installation de Docker, sans démon root (compte `claude`)

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
printf "Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n" \
  "$VERSION_CODENAME" "$(dpkg --print-architecture)" | sudo tee /etc/apt/sources.list.d/docker.sources
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-ce-rootless-extras uidmap
# Pas de démon root :
sudo systemctl disable --now docker.service docker.socket containerd
```

Installé : Docker 29.9.3, containerd v2.4.2, rootlesskit 3.3.0.
`/etc/subuid` et `/etc/subgid` contenaient déjà `hermes:165536:65536` : rien à ajouter.
Ubuntu 26.04 restreint les espaces de noms utilisateur non privilégiés
(`kernel.apparmor_restrict_unprivileged_userns = 1`), mais le paquet fournit le profil AppArmor
`/etc/apparmor.d/rootlesskit` : aucun réglage supplémentaire n'a été nécessaire.

## 2. Docker rootless pour `hermes`

```bash
sudo -iu hermes bash -s <<'EOF'
export XDG_RUNTIME_DIR=/run/user/1001 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1001/bus
dockerd-rootless-setuptool.sh install      # crée ~/.config/systemd/user/docker.service et le contexte CLI "rootless"
systemctl --user enable --now docker
EOF
```

Le linger était déjà activé pour `hermes` : le démon rootless démarre au boot.
Le contexte `rootless` est le contexte courant de `~/.docker/config.json` : la CLI `hermes` trouve le
démon sans variable. Pour la gateway, un drop-in fixe `DOCKER_HOST` et l'ordre de démarrage
(`.env` non modifié) :

```bash
# ~/.config/systemd/user/hermes-gateway.service.d/docker-rootless.conf (utilisateur hermes)
[Unit]
Wants=docker.service
After=docker.service

[Service]
Environment="DOCKER_HOST=unix:///run/user/1001/docker.sock"
```

```bash
systemctl --user daemon-reload
```

Vérifications :

| Commande | Résultat |
|---|---|
| `docker run --rm hello-world` (en `hermes`) | `Hello from Docker!` |
| `docker info --format '{{.SecurityOptions}}'` | `[name=seccomp,profile=builtin name=rootless name=cgroupns]` |
| `docker run --rm alpine cat /proc/self/uid_map` | `0 1001 1` / `1 165536 65536` (root du conteneur = `hermes` sur l'hôte) |
| `id hermes` | `uid=1001(hermes) gid=1001(hermes) groups=1001(hermes),100(users)` : pas de groupe `docker` |
| `getent group docker` | `docker:x:986:` (aucun membre) |
| `sudo -l -U hermes` | `User hermes is not allowed to run sudo` |
| `systemctl is-active docker.service docker.socket containerd` (système) | `inactive` ×3, `disabled` ×3 |
| `ps -eo user,comm` | `dockerd`, `containerd`, `rootlesskit` tournent sous `hermes` |
| Environnement du processus gateway (`/proc/<pid>/environ`) | `DOCKER_HOST=unix:///run/user/1001/docker.sock` |

## 3. Bascule de Hermes

```bash
sudo -iu hermes bash -s <<'EOF'
cp -p ~/.hermes/config.yaml ~/.hermes/config.yaml.bak-sandbox
hermes config set terminal.backend docker
hermes config set terminal.docker_mount_cwd_to_workspace false
docker pull nousresearch/hermes-sandbox:desktop      # image par défaut (4,4 Go), préchargée
EOF
```

Contrôle de la config (`terminal:`) : pas de `docker_forward_env`, `docker_env`, `docker_volumes`,
`docker_extra_args`, ni `env_passthrough` / `credential_files`. Aucune skill ne déclare
`required_environment_variables` (passage implicite de variables). Les seules occurrences de
« todoist » dans `config.yaml` sont le nom du plugin.

### Outil resté sur l'hôte : `browser` désactivé

Audit des outils actifs (`hermes tools list --platform cli|telegram|cron`) : `browser_exec` exécute le
code Python fourni par le modèle avec l'interpréteur de Hermes **sur l'hôte**
(`tools/browser_use_cli.py` : `[sys.executable, "-m", "browser_harness.run"]`, code passé sur stdin).
C'est un contournement complet du conteneur (lecture de `.env` possible). Désactivé :

```bash
hermes tools disable --platform cli browser
hermes tools disable --platform telegram browser
hermes tools disable --platform cron browser
hermes config set agent.disabled_toolsets browser   # liste de refus globale : un job cron ne peut pas la réélargir
```

Puis redémarrage : `systemctl --user restart hermes-gateway` (état : `active`).

Autres outils actifs examinés (code de v0.23.1), aucun ne lit de fichier arbitraire ni ne lance de
commande sur l'hôte :

| Outil | Constat |
|---|---|
| `terminal`, `process_manage`, `execute_code`, `read_file`, `write_file`, `patch`, `search_files` | Dans le conteneur (`docker exec`), vérifié ci-dessous. |
| `vision_analyze` | Sur un backend non local, lit sur l'hôte uniquement dans les caches média ; sinon lecture dans le conteneur (`tools/image_source.py`). |
| `computer_use` | `bot_desktop.placement: auto` + backend docker ⇒ écran et pilote dans le conteneur (`tools/bot_desktop/placement.py`). |
| `skill_view`, `skill_manage` | Confinés à `~/.hermes/skills` (refus de `..`, `validate_within_dir`). Écriture sur l'hôte possible dans ce dossier, monté en lecture seule dans le conteneur. |
| `cronjob_manage` | Les scripts doivent être des fichiers de `~/.hermes/scripts/` (dossier absent, non inscriptible depuis le conteneur) ; les jobs agents respectent `agent.disabled_toolsets`. |
| `delegate_task` | Les sous-agents n'ont jamais plus d'outils que le parent, même conteneur. |
| `memory`, `session_search`, `todo_list`, `clarify` | Données internes de Hermes (mémoire, historique), pas d'accès fichier libre. |
| `web_search`, `web_extract` | `http`/`https` seulement (`tools/url_safety.py`), pas de `file://`. |
| `image_generate`, `text_to_speech`, `manage_connections` | Services distants / cache audio. |
| Livraison `MEDIA:` de la gateway | Liste blanche de dossiers (`gateway/media_policy.py`). |
| Plugin `todoist` | Outils `propose_*` : cartes à valider, aucune écriture Todoist. |

## 4. Vérification de l'isolation

Le conteneur (`docker inspect`, en `hermes`) :

- Montages : `sandboxes/docker/default/home -> /root` et `.../workspace -> /workspace` (rw), puis en
  lecture seule `attachments`, `composer-pastes`, `images`, `skills`, `cache/{audio,delegation,documents,generated,images,screenshots,spillover,videos,web}`
  sous `/root/.hermes/`. Ni `.env`, ni `config.yaml`, ni `plugin-data`, ni `plugins`, ni `state.db`.
- Recherche de chaque valeur de `.env` dans tous les dossiers montés : 0 fichier pour chaque clé.
- Variables : `PATH GPG_KEY PYTHON_VERSION PYTHON_SHA256 POETRY_HOME DEBIAN_FRONTEND PLAYWRIGHT_BROWSERS_PATH LANG XDG_RUNTIME_DIR`.
  `env | grep -ci todoist` ⇒ `0` ; aucun `/proc/*/environ` du conteneur ne contient `todoist`.
- `CapDrop=[ALL]`, `CapAdd=[CHOWN DAC_OVERRIDE FOWNER SETGID SETUID]`, `no-new-privileges`, non privilégié.

Par l'agent : `hermes chat -q "<consigne>" --oneshot -Q </dev/null`, lancé par
`~hermes/sandbox-check/run.sh`, qui masque toute valeur de `.env` trouvée dans la sortie et l'indique.
Les trois exécutions : « SECRET VALUES FOUND IN OUTPUT: none ».

| Test | Résultat |
|---|---|
| terminal `id; hostname` | `uid=0(root)` (= `hermes` sur l'hôte), `3809fa432236` (conteneur ; l'hôte est `vps-3536aa33`) |
| terminal `cat /home/hermes/.hermes/.env` | `No such file or directory` |
| terminal `env \| grep -i todoist` | vide (rc=1) |
| terminal `ls -la /home/hermes` | `No such file or directory` |
| `read_file /home/hermes/.hermes/.env` | refusé (garde « credential store ») ; et le fichier n'existe pas dans le conteneur |
| `read_file /etc/hostname` | `3809fa432236` ⇒ l'outil lit dans le conteneur |
| `read_file /home/hermes/.hermes/SOUL.md` (existe sur l'hôte) | `File not found` |
| `write_file /tmp/probe-sandbox.txt` | réussi dans le conteneur ; absent de `/tmp` sur l'hôte |
| `search_files` dans `/home/hermes` | `Path not found: /home/hermes` |
| `search_files` « TODOIST » dans `/home`, `/etc`, `/opt` | 0 résultat |
| `execute_code` : `os.path.exists("/home/hermes/.hermes/.env")`, variables `TODOIST*` | `False []` |
| terminal `curl https://api.todoist.com/api/v1/projects` (sans clé) | `todoist_http=401` |
| terminal `ls /run/user/1001/docker.sock /var/run/docker.sock` | absents (pas d'accès au démon depuis le conteneur) |
| `hermes chat -q "Liste mes projets Todoist" --oneshot -Q` | « Inbox, Bot-test » : le plugin fonctionne |

Restent à faire par l'utilisateur sur Telegram (recette §9) : même tentative de lecture de la clé et
d'appel Todoist depuis l'agent, et vérification que les cartes et boutons fonctionnent toujours.

## Retour arrière

```bash
sudo -iu hermes bash -c 'cp -p ~/.hermes/config.yaml.bak-sandbox ~/.hermes/config.yaml && XDG_RUNTIME_DIR=/run/user/1001 systemctl --user restart hermes-gateway'
```

(Le retour à `terminal.backend: local` rend de nouveau la clé lisible par l'agent.)

## Après chaque `hermes update`

Relancer `hermes tools list --platform telegram` et vérifier qu'aucun nouvel outil ne s'exécute sur
l'hôte, puis relancer les tests ci-dessus avec `~hermes/sandbox-check/run.sh "<consigne>"`.
