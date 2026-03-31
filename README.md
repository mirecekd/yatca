# YATCA — Yet Another Telegram Connector for Agent-zero

---

<div align="center">
<img src="yatca_ghcover.jpg" alt="YATCA" width="60%">
</div>

---

A full-featured Telegram bot bridge for [Agent Zero](https://github.com/frdel/agent-zero). Send messages, photos, and files to your Agent Zero instance directly from Telegram.

<div align="center">

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/mirecekdg)

</div>

---

## Features

- **Text messages** — forwarded to Agent Zero, responses streamed back
- **Photos** — sent as base64 attachments for vision analysis
- **Documents/files** — forwarded as attachments (up to 20 MB)
- **Access control** — whitelist by Telegram User ID and/or Chat ID
- **Markdown rendering** — Agent Zero markdown converted to Telegram HTML
- **Long message splitting** — auto-splits responses exceeding Telegram's 4096 char limit
- **Agent control** — pause, resume, nudge stuck agents
- **Context window info** — check token usage
- **Task scheduler** — list and trigger scheduled tasks with inline buttons
- **Project switching** — switch active A0 project per Telegram chat
- **State persistence** — remembers context IDs and selected projects across restarts
- **Auto-reconnect** — CSRF session management with automatic re-authentication
- **Supervisord managed** — auto-restart on crash
- **A0-aware startup** — waits for Agent Zero `/health` before launching the bridge

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Start the bot |
| `/help` | Show available commands |
| `/reset` | Start a new conversation |
| `/status` | Show connection status |
| `/id` | Show your User/Chat ID |
| `/stop` | Pause the agent (stop current work) |
| `/resume` | Resume a paused agent |
| `/nudge` | Kick the agent when stuck |
| `/context` | Show context window info |
| `/tasks` | List scheduled tasks (with Run buttons) |
| `/project` | List and switch A0 projects |

---

## Quick Install (Paste into Agent Zero)

The easiest way to install YATCA is to **paste the following instruction directly into your Agent Zero chat**.

> Before pasting, make sure you have your **Telegram Bot Token** ready (get one from [@BotFather](https://t.me/BotFather)).

### Paste this into Agent Zero:

```text
Install YATCA into this Agent Zero container and configure it for autostart.

Download repo from https://github.com/mirecekd/yatca

Do the following exactly:

1. Install Python dependencies into /opt/venv:
   pip install aiohttp python-telegram-bot python-dotenv

2. Download these files from the YATCA repository and save them exactly here:
   - /a0/usr/workdir/telegram_bridge.py
   - /a0/usr/workdir/yatca_run.sh

3. Make the runner executable:
   chmod +x /a0/usr/workdir/yatca_run.sh

4. Add these variables to /a0/usr/.env if they are missing:
   TELEGRAM_BOT_TOKEN=<I WILL PROVIDE THIS>
   TELEGRAM_USER_IDS=<MY TELEGRAM USER ID>

   Optional:
   TELEGRAM_CHAT_IDS=
   A0_API_URL=http://127.0.0.1:80/api_message
   A0_TIMEOUT=300
   MAX_FILE_SIZE_MB=20
   YATCA_STATE_FILE=/a0/usr/workdir/yatca_state.json

5. Register YATCA in the ACTIVE supervisord config used by this container.
   Important: detect which supervisord config is actually used by PID 1.
   In this Agent Zero container it may be /etc/supervisor/conf.d/supervisord.conf.

   Add this program section if it is not present yet:

   [program:telegram_bridge]
   command=/a0/usr/workdir/yatca_run.sh
   directory=/a0
   user=root
   autostart=true
   autorestart=true
   startsecs=5
   startretries=20
   stopwaitsecs=20
   stdout_logfile=/dev/stdout
   stdout_logfile_maxbytes=0
   stderr_logfile=/dev/stderr
   stderr_logfile_maxbytes=0
   stopasgroup=true
   killasgroup=true
   environment=PYTHONUNBUFFERED="1",A0_HEALTH_URL="http://127.0.0.1/health",YATCA_A0_WAIT_TIMEOUT="300",YATCA_A0_CHECK_INTERVAL="2"
   priority=200

6. Reload supervisord and ensure telegram_bridge exists and starts.

7. Verify with:
   supervisorctl status telegram_bridge

Important behavior requirement:
- YATCA must NOT start immediately.
- It must wait until Agent Zero health endpoint returns HTTP 200 on /health.
- Do not add extra waiting for mount paths. Waiting for /health is the intended startup gate.

My Telegram Bot Token is: <PASTE YOUR TOKEN HERE>
My Telegram User ID is: <PASTE YOUR USER ID HERE>
```

> **Tip:** Don't know your Telegram User ID? Start the bot without `TELEGRAM_USER_IDS`, send `/id`, then add your ID and restart YATCA.

---

## Manual Installation

### Prerequisites

- Agent Zero running in Docker
- Telegram Bot Token from [@BotFather](https://t.me/BotFather)
- Access to the running container shell

### Step 1: Copy files into the container

```bash
docker cp telegram_bridge.py agent-zero:/a0/usr/workdir/telegram_bridge.py
docker cp yatca_run.sh agent-zero:/a0/usr/workdir/yatca_run.sh
docker exec -it agent-zero chmod +x /a0/usr/workdir/yatca_run.sh
```

### Step 2: Install dependencies

```bash
docker exec -it agent-zero /opt/venv/bin/pip install aiohttp python-telegram-bot python-dotenv
```

### Step 3: Configure environment variables

Add to `/a0/usr/.env` inside the container:

```env
# === YATCA Telegram Bridge ===
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...

# Access control (comma-separated, leave empty to allow all)
TELEGRAM_USER_IDS=123456789
TELEGRAM_CHAT_IDS=

# Optional overrides
A0_API_URL=http://127.0.0.1:80/api_message
A0_TIMEOUT=300
MAX_FILE_SIZE_MB=20
YATCA_STATE_FILE=/a0/usr/workdir/yatca_state.json
```

> The bridge also reads `AUTH_LOGIN` and `AUTH_PASSWORD` from `.env` for internal API authentication used by commands like `/stop`, `/resume`, `/nudge`, `/tasks`, and `/project`.

### Step 4: Add supervisord program

First detect the active supervisord config:

```bash
docker exec -it agent-zero ps -ef | grep supervisord
```

In many Agent Zero containers the active file is:

```text
/etc/supervisor/conf.d/supervisord.conf
```

Add this block to the **active** config file:

```ini
[program:telegram_bridge]
command=/a0/usr/workdir/yatca_run.sh
directory=/a0
user=root
autostart=true
autorestart=true
startsecs=5
startretries=20
stopwaitsecs=20
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0
stopasgroup=true
killasgroup=true
environment=PYTHONUNBUFFERED="1",A0_HEALTH_URL="http://127.0.0.1/health",YATCA_A0_WAIT_TIMEOUT="300",YATCA_A0_CHECK_INTERVAL="2"
priority=200
```

### Step 5: Reload supervisord

```bash
docker exec -it agent-zero supervisorctl reread
docker exec -it agent-zero supervisorctl update
```

### Step 6: Verify

```bash
docker exec -it agent-zero supervisorctl status telegram_bridge
```

If everything is correct, YATCA will:

1. start under supervisord
2. wait for `GET /health` on Agent Zero
3. launch `telegram_bridge.py`
4. auto-restart if the bridge crashes

---

## Startup Model

YATCA intentionally uses **A0 health readiness** as the startup gate.

It does **not** wait on arbitrary mount paths.

The runner logic is:

1. ensure Python dependencies are installed
2. poll `http://127.0.0.1/health`
3. continue only after HTTP 200
4. `exec` the Telegram bridge

Current runner file:

- `/a0/usr/workdir/yatca_run.sh`

---

## Included helper scripts

| File | Purpose |
|---|---|
| `telegram_bridge.py` | Main Telegram bridge |
| `yatca_run.sh` | Waits for A0 `/health`, then starts the bridge |
| `yatca_startup.sh` | Legacy installer/helper script retained for reference/manual setup |

---

## Configuration Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | yes | — | Bot token from @BotFather |
| `A0_API_URL` | no | `http://127.0.0.1:80/api_message` | Agent Zero API endpoint |
| `A0_TIMEOUT` | no | `300` | Request timeout in seconds |
| `A0_API_KEY` | no | auto-detected | API key (auto-read from A0 settings) |
| `AUTH_LOGIN` | no | — | A0 web UI login for internal API auth |
| `AUTH_PASSWORD` | no | — | A0 web UI password for internal API auth |
| `TELEGRAM_USER_IDS` | no | — | Comma-separated allowed Telegram user IDs |
| `TELEGRAM_CHAT_IDS` | no | — | Comma-separated allowed Telegram chat IDs |
| `MAX_FILE_SIZE_MB` | no | `20` | Max file size for attachments in MB |
| `YATCA_STATE_FILE` | no | `/a0/usr/workdir/yatca_state.json` | Persistent state file for contexts/projects |
| `A0_HEALTH_URL` | no | `http://127.0.0.1/health` | Health endpoint checked by `yatca_run.sh` |
| `YATCA_A0_WAIT_TIMEOUT` | no | `300` | Max seconds to wait for A0 health |
| `YATCA_A0_CHECK_INTERVAL` | no | `2` | Poll interval in seconds |

---

## Architecture

```text
+-------------+         +------------------+         +-------------+
|  Telegram   |  HTTPS  |   YATCA Bridge   |  HTTP   | Agent Zero  |
|   (User)    |<------->| (python-tg-bot)  |<------->|   API/UI    |
+-------------+         +------------------+         +-------------+
                                |
                                |--- /api_message         (X-API-KEY auth)
                                |--- /pause               (Session + CSRF)
                                |--- /nudge               (Session + CSRF)
                                |--- /ctx_window_get      (Session + CSRF)
                                |--- /scheduler_tasks_list(Session + CSRF)
                                +--- /projects            (Session + CSRF)
```

The bridge uses two authentication methods:

- **`/api_message`** — API key authentication (`X-API-KEY` header)
- **other internal endpoints** — session authentication (login + CSRF token), handled automatically by `A0SessionManager`

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Bot doesn't respond | Check `supervisorctl status telegram_bridge` |
| YATCA starts too early | Verify the program uses `/a0/usr/workdir/yatca_run.sh`, not direct `python3 telegram_bridge.py` |
| `ERROR (no such process)` in supervisorctl | You likely edited the wrong supervisord config; inspect the active config used by PID 1 |
| `CSRF token missing` | Ensure `AUTH_LOGIN` and `AUTH_PASSWORD` are set in `.env` |
| `API key required` | Check that A0 settings expose an MCP server token or set `A0_API_KEY` explicitly |
| `/tasks` or `/project` returns error | Verify A0 web UI credentials in `.env` |
| Bot ignores messages | Check `TELEGRAM_USER_IDS` / `TELEGRAM_CHAT_IDS` whitelist |

---

## License

MIT

---

## Support

If you find YATCA useful, consider buying me a coffee!

<div align="center">

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/mirecekdg)

</div>

## Credits

YATCA was inspired by and built upon these projects:

- [winboost/agent-zero-telegram-bridge](https://github.com/winboost/agent-zero-telegram-bridge) — the original Telegram bridge for Agent Zero
- [seqis/Agent Zero to Telegram Bridge](https://gist.github.com/seqis/69ba87a3d8c552b94b8a6bf9612b1c28) — how-to guide for building an A0 Telegram bridge

---
