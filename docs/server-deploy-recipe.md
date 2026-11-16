# Deploy a Python Telegram bot to a shared server — recipe

> **Note (this repo):** this file is the generic reference recipe. One deliberate
> deviation: this repo's `deploy.sh` ships the committed tree via
> `git archive | ssh ... tar -x` instead of §6's `git clone`/`pull`, so the server
> needs no Git credentials. It reads the SSH target from `DEPLOY_SSH` (environment
> or an untracked `deploy.local.env`, template `deploy.local.env.example`). The venv
> is (re)built on the server's architecture automatically by `uv sync` during deploy.

This recipe deploys a Python Telegram bot to a shared Linux server on which you have
an **unprivileged** account — **no root, no sudo**. That's fine: everything below is
designed to run entirely as your own user. Do **not** try to `sudo`, edit anything
under `/etc`, `/opt`, touch other users' files, or install system packages — those
will fail and aren't needed.

Placeholders used throughout: `<user>` (your account on the server), `<host>`
(server hostname), `<port>` (SSH port). Values marked **[FILL IN]** are yours to
supply.

---

## 1. The environment (prerequisites set up by the server administrator)

| Thing | Value |
|---|---|
| SSH login | `ssh -p <port> <user>@<host>` |
| OS | a Linux distribution with systemd (user services supported) |
| Your home | `/home/<user>` (only you and root can read it) |
| Python tooling | `uv` installed, e.g. at `/usr/local/bin/uv` (just call `uv`) |
| Process supervisor | **systemd user services** — you manage your own services with `systemctl --user`, no root needed |
| Lingering | **Enabled** for your account — your services start at boot and keep running after you log out |
| Git | available (`/usr/bin/git`) |

### Why this works without root: systemd *user* services

Normally running a long-lived service (like a Telegram bot) means a root-owned
`/etc/systemd/system/*.service` unit. Instead, you use **per-user units** in
`~/.config/systemd/user/`, managed with `systemctl --user ...`. Once the administrator
has enabled **linger** for your account, your personal systemd instance runs at boot
and supervises your bot 24/7 — restarts on crash, starts on reboot — all without root.

Lean on this pattern; don't reinvent supervision with `nohup`, `screen`, `tmux`, or
cron.

### Important: use **long polling**, not webhooks

On a typical shared server the firewall only allows inbound traffic on a few ports,
and opening a new one (plus a public HTTPS endpoint) requires the administrator.
**Outbound** traffic is usually open. So the bot should talk to Telegram via **long
polling** (`getUpdates`), which is purely outbound — the default mode in every major
Python Telegram library (`python-telegram-bot`, `aiogram`, `pyrogram`, `telebot`).

- ✅ Long polling → works without root, nothing to ask anyone for.
- ❌ Webhooks → need an open firewall port, a reverse-proxy vhost, and a
  subdomain/cert from the administrator. **Do not attempt webhooks** in this setup;
  if you really need them, that's a request to the administrator and out of scope here.

### What you can and cannot do

- ✅ Clone repos, create virtualenvs with `uv`, install Python deps, run your bot,
  manage your own systemd `--user` services, read your own logs via `journalctl --user`.
- ❌ `sudo` / root, install OS packages (`apt`), bind ports below 1024, open firewall
  ports, edit the web server config, read or modify other users' files.
- If the bot needs a **non-Python system dependency** (e.g. `ffmpeg`, `libpq`,
  Playwright browsers), that needs the administrator. Prefer pure-Python deps where
  possible; otherwise list what's needed and ask the administrator.

---

## 2. One-time SSH access check

First confirm your **public** key has been installed for your account.

```bash
ssh -p <port> <user>@<host> 'echo OK: $(whoami)@$(hostname); uv --version; systemctl --user is-system-running'
```

Expected: `OK: <user>@...`, a `uv` version, and `running` (or `degraded`, which is
also fine — it just means some other user unit failed, not yours).

If you get `Permission denied (publickey)`, the key isn't installed yet — send your
**public** key (`~/.ssh/id_ed25519.pub`) to the administrator and wait. Don't try to
fix auth from your side.

> Note on `systemctl --user` over SSH: a normal interactive login sets the needed
> environment automatically. For **non-interactive** one-shot SSH commands (like the
> deploy script below), always prefix with the two env exports shown — `.profile`
> isn't sourced for non-interactive shells. The deploy script handles this for you.

---

## 3. Project layout the bot repo should have

Structure the bot repo (hosted anywhere, or a local `git init`) like:

```
mybot/
├── pyproject.toml        # deps, managed by uv
├── bot.py                # entrypoint: starts polling
├── .env.example          # documents required env vars (NO real secrets)
└── README.md
```

Minimal `pyproject.toml` (adjust name/deps to your bot):

```toml
[project]
name = "mybot"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "python-telegram-bot>=21",   # or aiogram>=3, etc.
]
```

The entrypoint **must read the bot token from an environment variable**, never
hardcode it. Example with `python-telegram-bot`:

```python
# bot.py
import os
from telegram.ext import Application, CommandHandler

async def start(update, ctx):
    await update.message.reply_text("hi from the server 👋")

def main():
    token = os.environ["BOT_TOKEN"]          # injected by systemd, see §5
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.run_polling()                         # long polling — outbound only

if __name__ == "__main__":
    main()
```

`.env.example` (committed) documents what's needed; the real values live only on the
server (next section) and are **never committed**:

```
BOT_TOKEN=         # from @BotFather
```

---

## 4. The secret (bot token) lives only on the server

**[FILL IN]** your bot token from [@BotFather](https://t.me/BotFather) — but do
**not** put it in the repo or in logs you keep. Write it directly into a private env
file on the server (mode 600, in your home — only you and root can read it):

```bash
ssh -p <port> <user>@<host> 'install -d -m 700 ~/etc'
# Then create ~/etc/mybot.env with the token (mode 600). Paste the token;
# avoid echoing it into your local shell history if possible.
ssh -p <port> <user>@<host> 'umask 077; cat > ~/etc/mybot.env' <<'EOF'
BOT_TOKEN=PASTE_THE_REAL_TOKEN_HERE
EOF
ssh -p <port> <user>@<host> 'chmod 600 ~/etc/mybot.env; ls -l ~/etc/mybot.env'
```

systemd will load this file into the bot's environment (`EnvironmentFile=`), so the
token never appears in the unit file, the repo, or the process command line.

---

## 5. The systemd **user** service unit

Create `~/.config/systemd/user/mybot.service` on the server. Template:

```ini
[Unit]
Description=My Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/apps/mybot
EnvironmentFile=%h/etc/mybot.env
# uv resolves/creates the venv on first run; --frozen keeps it reproducible from uv.lock
ExecStart=/usr/local/bin/uv run --frozen python bot.py
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=default.target
```

Notes:
- `%h` = your home dir; keeps the unit portable.
- `WantedBy=default.target` (the **user** default target), **not** `multi-user.target`.
- No `User=`/`Group=` — user units already run as you.
- Keep the bot in `~/apps/mybot` (the deploy script below uses that path).

---

## 6. Deploy script (run from your local machine)

Put this `deploy.sh` in the bot repo. It's the whole deployment: push code, pull on
the server, sync deps with `uv`, install/refresh the unit, (re)start it, and show
status. Everything runs as your own user — no root anywhere.

```bash
#!/usr/bin/env bash
set -euo pipefail

SSH="ssh -p <port> <user>@<host>"   # [FILL IN]
APP=mybot
REPO_URL="<repo-url>"               # [FILL IN] needs read access from the server
REMOTE_DIR="apps/$APP"

# Env so `systemctl --user` works over non-interactive SSH (linger guarantees the path exists)
ENVSET='export XDG_RUNTIME_DIR=/run/user/$(id -u); export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus'

echo "==> Push local commits"
git push

echo "==> Clone or update on server"
$SSH "
  set -e
  mkdir -p ~/apps
  if [ -d ~/$REMOTE_DIR/.git ]; then
    cd ~/$REMOTE_DIR && git pull --ff-only
  else
    git clone $REPO_URL ~/$REMOTE_DIR
  fi
"

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
```

Keep the `mybot.service` file from §5 in the repo at `deploy/mybot.service` so the
script can copy it. The `~/etc/mybot.env` secret stays out of the repo (§4).

---

## 7. Operating the bot (all as your own user, no root)

Run these after `ssh -p <port> <user>@<host>` (interactive login sets the env
automatically):

```bash
systemctl --user status mybot         # is it running?
systemctl --user restart mybot        # restart
systemctl --user stop mybot           # stop
journalctl --user -u mybot -f         # live logs
journalctl --user -u mybot -n 100     # last 100 log lines
systemctl --user enable mybot         # start on boot (done by deploy.sh already)
```

Redeploy after code changes: commit, then run `./deploy.sh` again.

---

## 8. Guardrails — what to do when you hit a wall

- **Needs an OS package** (`apt install ...`) → you can't; list it and ask the
  administrator.
- **Needs an inbound port / webhook / public URL / subdomain** → needs the
  administrator. Use polling instead (§1); only escalate if genuinely impossible.
- **`Permission denied` writing somewhere** → you're outside your home. Keep everything
  under `/home/<user>` (`~/apps`, `~/etc`, `~/.config/systemd/user`).
- **`systemctl --user` says "Failed to connect to bus"** → you're in a non-interactive
  SSH command without the env exports. Prefix with:
  `export XDG_RUNTIME_DIR=/run/user/$(id -u); export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus`
- **Bot won't start** → `journalctl --user -u mybot -n 50` for the traceback. Usual
  causes: missing `BOT_TOKEN` (check `~/etc/mybot.env` exists, mode 600, correct var
  name), dependency not in `pyproject.toml`, or a second copy of the bot already
  polling (Telegram rejects two pollers on one token — stop the duplicate).
- **Never** commit the bot token or any secret to the repo.

---

### TL;DR
1. Verify SSH + `uv` + `systemctl --user` (§2).
2. Make sure the bot reads `BOT_TOKEN` from env and uses **polling** (§3).
3. Put the token in `~/etc/mybot.env` (mode 600) on the server (§4).
4. Ship the `mybot.service` user unit (§5) and the `deploy.sh` (§6).
5. Run `deploy.sh`; confirm with `systemctl --user status mybot` and the logs (§7).
