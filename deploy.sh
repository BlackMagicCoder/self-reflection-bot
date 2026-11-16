#!/usr/bin/env bash
set -euo pipefail

# The SSH target comes from the environment or from an untracked
# deploy.local.env next to this script (template: deploy.local.env.example).
# A DEPLOY_SSH already set in the environment wins over the file.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -z "${DEPLOY_SSH-}" ] && [ -f "$SCRIPT_DIR/deploy.local.env" ]; then
  # shellcheck disable=SC1091
  . "$SCRIPT_DIR/deploy.local.env"
fi

if [ -z "${DEPLOY_SSH-}" ]; then
  echo "error: DEPLOY_SSH is not set." >&2
  echo "Set it in the environment or in deploy.local.env (copy deploy.local.env.example)," >&2
  echo "e.g. DEPLOY_SSH=\"ssh -p 22 user@example.org\"" >&2
  exit 1
fi

SSH="$DEPLOY_SSH"
APP=self-reflection
# Relative to the remote user's home; must match WorkingDirectory in deploy/$APP.service.
REMOTE_DIR="apps/$APP"

# Env so `systemctl --user` works over non-interactive SSH (linger guarantees the path exists)
ENVSET='export XDG_RUNTIME_DIR=/run/user/$(id -u); export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus'

echo "==> Push local commits"
git push

# Ship the committed tree directly over SSH instead of cloning, so the server
# needs no Git credentials.
echo "==> Copy committed tree to server"
$SSH "mkdir -p ~/$REMOTE_DIR"
git archive --format=tar HEAD | $SSH "tar -x -C ~/$REMOTE_DIR"

echo "==> Sync Python dependencies with uv"
$SSH "cd ~/$REMOTE_DIR && uv sync --frozen"

echo "==> Install/refresh systemd user unit"
$SSH "
  mkdir -p ~/.config/systemd/user
  cp ~/$REMOTE_DIR/deploy/$APP.service ~/.config/systemd/user/$APP.service
  $ENVSET
  systemctl --user daemon-reload
  systemctl --user enable $APP.service
  systemctl --user restart $APP.service
"

echo "==> Status"
$SSH "$ENVSET; systemctl --user --no-pager status $APP.service | head -15"

echo "==> Done. Logs:  $SSH '$ENVSET; journalctl --user -u $APP -f'"
