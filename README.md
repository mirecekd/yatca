# YATCA — Yet Another Telegram Connector for Agent-zero

<div align="center">

<img src="yatca_ghcover.jpg" alt="YATCA" width="40%">

</div>

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
- **Auto-reconnect** — CSRF session management with automatic re-authentication
- **Supervisord managed** — auto-restart on crash

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

---

## Quick Install (Paste into Agent Zero)

The easiest way to install YATCA is to **paste the following instruction directly into your Agent Zero chat**. The agent will handle everything automatically:

> Before pasting, make sure you have your **Telegram Bot Token** ready (get one from [@BotFather](https://t.me/BotFather)).

### Paste this into Agent Zero:

```
Install the YATCA Telegram bridge for me. Here is what you need to do:

1. Install Python dependencies:
 pip install aiohttp python-telegram-bot python-dotenv

2. Download telegram_bridge.py from https://raw.githubusercontent.com/mirecekd/yatca/main/telegram_bridge.py
 and save it to /a0/usr/workdir/telegram_bridge.py

3. Add these variables to /a0/usr/.env (if not already present):
 TELEGRAM_BOT_TOKEN=<I WILL PROVIDE THIS>
 TELEGRAM_USER_IDS=<MY TELEGRAM USER ID>

4. Create supervisord config by appending this to /etc/supervisor/conf.d/supervisord.conf:

[program:telegram_bridge]
command=/opt/venv/bin/python3 /a0/usr/workdir/telegram_bridge.py
environment=
user=root
directory=/a0
stopwaitsecs=10
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0
autorestart=true
startretries=3

5. Reload supervisord and start the bridge:
 supervisorctl reread && supervisorctl update && supervisorctl start telegram_bridge

6. Verify it's running:
 supervisorctl status telegram_bridge

My Telegram Bot Token is: <PASTE YOUR TOKEN HERE>
My Telegram User ID is: <PASTE YOUR USER ID HERE>
```

> **Tip:** Don't know your Telegram User ID? Start the bot first without `TELEGRAM_USER_IDS` restriction, send `/id` to the bot, then add your ID to `.env` and restart.

---

## Manual Installation

### Prerequisites

- Agent Zero running in Docker
- Telegram Bot Token from [@BotFather](https://t.me/BotFather)

### Step 1: Install dependencies

```bash
docker exec -it agent-zero /opt/venv/bin/pip install aiohttp python-telegram-bot python-dotenv
```

### Step 2: Copy the bridge script

```bash
docker cp telegram_bridge.py agent-zero:/a0/usr/workdir/telegram_bridge.py
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
```

> The bridge also reads `AUTH_LOGIN` and `AUTH_PASSWORD` from `.env` for internal API authentication (CSRF-protected endpoints like `/pause`, `/nudge`, `/tasks`). These are typically already set if you use Agent Zero's web UI login.

### Step 4: Set up supervisord

Append to `/etc/supervisor/conf.d/supervisord.conf`:

```ini
[program:telegram_bridge]
command=/opt/venv/bin/python3 /a0/usr/workdir/telegram_bridge.py
environment=
user=root
directory=/a0
stopwaitsecs=10
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0
autorestart=true
startretries=3
```

### Step 5: Start the bridge

```bash
docker exec -it agent-zero supervisorctl reread
docker exec -it agent-zero supervisorctl update
docker exec -it agent-zero supervisorctl start telegram_bridge
```

### Step 6: Verify

```bash
docker exec -it agent-zero supervisorctl status telegram_bridge
```

Expected output:
```
telegram_bridge RUNNING pid 1234, uptime 0:00:05
```

---

## Configuration Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | | — | Bot token from @BotFather |
| `A0_API_URL` | | `http://127.0.0.1:80/api_message` | Agent Zero API endpoint |
| `A0_TIMEOUT` | | `300` | Request timeout in seconds |
| `A0_API_KEY` | | auto-detected | API key (auto-read from A0 settings) |
| `AUTH_LOGIN` | | — | A0 web UI login (for internal API CSRF auth) |
| `AUTH_PASSWORD` | | — | A0 web UI password (for internal API CSRF auth) |
| `TELEGRAM_USER_IDS` | | — | Comma-separated allowed Telegram user IDs |
| `TELEGRAM_CHAT_IDS` | | — | Comma-separated allowed Telegram chat IDs |
| `MAX_FILE_SIZE_MB` | | `20` | Max file size for attachments in MB |

---

## Architecture

```
+-------------+         +------------------+         +-------------+
|  Telegram   |  HTTPS  |   YATCA Bridge   |  HTTP   | Agent Zero  |
|   (User)    |<------->| (python-tg-bot)  |<------->| (Flask API) |
+-------------+         +------------------+         +-------------+
                                |
                                |--- /api_message  (X-API-KEY auth)
                                |--- /pause        (Session + CSRF)
                                |--- /nudge        (Session + CSRF)
                                |--- /ctx_window_get (Session + CSRF)
                                +--- /scheduler_tasks_list (Session + CSRF)
```

The bridge uses two authentication methods:
- **`/api_message`** -- API key authentication (`X-API-KEY` header)
- **All other endpoints** -- Full session authentication (login + CSRF token), managed automatically by `A0SessionManager`

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Bot doesn't respond | Check `supervisorctl status telegram_bridge` |
| `CSRF token missing` | Ensure `AUTH_LOGIN` and `AUTH_PASSWORD` are set in `.env` |
| `API key required` | Check that A0 settings have an MCP server token configured |
| `/tasks` returns error | Verify A0 web UI login credentials in `.env` |
| `bad escape \U` | Unicode escape issue — ensure the .py file contains actual emoji characters, not `\U` escape sequences |
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

- [winboost/agent-zero-telegram-bridge](https://github.com/winboost/agent-zero-telegram-bridge) -- the original Telegram bridge for Agent Zero
- [seqis/Agent Zero to Telegram Bridge](https://gist.github.com/seqis/69ba87a3d8c552b94b8a6bf9612b1c28) -- How-To guide for building an A0 Telegram bridge
