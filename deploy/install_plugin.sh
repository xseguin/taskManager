#!/usr/bin/env bash
# Installe le plugin Hermes todoist-assistant sur le VPS. Usage : deploy/install_plugin.sh <hôte-ssh>
# Le compte SSH doit avoir sudo sans mot de passe ; les fichiers sont écrits en tant que `hermes`.
set -euo pipefail
HOST=${1:?usage: $0 <hôte-ssh>}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
DEST=/home/hermes/.hermes/plugins/todoist-assistant
AS_HERMES="sudo -n -u hermes"

ssh "$HOST" "cd / && $AS_HERMES mkdir -p $DEST"
rsync -a --delete --filter='P assistant/' --exclude __pycache__ --rsync-path="$AS_HERMES rsync" \
    "$ROOT/hermes_plugin/" "$HOST:$DEST/"
rsync -a --delete --exclude __pycache__ --rsync-path="$AS_HERMES rsync" \
    "$ROOT/assistant/" "$HOST:$DEST/assistant/"

ssh "$HOST" "cd / && $AS_HERMES -H bash -l -s" <<'REMOTE'
set -eu
cd ~
# Toute commande lisant stdin reçoit `yes` ou /dev/null : sinon elle avalerait la suite du script.
# `yes` accepte la préparation des dépendances proposée par `hermes plugins enable`.
yes | hermes plugins enable todoist-assistant || { echo "échec de hermes plugins enable" >&2; exit 1; }
XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user restart hermes-gateway </dev/null
echo "todoist-assistant installé ; vérifier : journalctl --user -u hermes-gateway --since -2min"
REMOTE
