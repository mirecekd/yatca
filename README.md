# YATCA -- Yet Another Telegram Connector for Agent-zero

---

<div align="center">
<img src="yatca_ghcover.jpg" alt="YATCA" width="60%">
</div>

---

A full-featured Telegram bot plugin for [Agent Zero](https://github.com/frdel/agent-zero). Send messages, photos, and files to your Agent Zero instance directly from Telegram.

<div align="center">

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/mirecekdg)

</div>

---

## v2: Native Agent Zero Plugin

YATCA v2 is a **native Agent Zero plugin** that integrates directly with the A0 engine. No more standalone bridge scripts, supervisord configs, or `.env` files -- everything is managed through the A0 WebUI.

This repo is structured for the [a0-plugins](https://github.com/agent0ai/a0-plugins) community index. When Agent Zero installs the plugin, it clones this repo into `plugins/yatca/`.

### Key Changes from v1

| v1 (standalone bridge) | v2 (A0 plugin) |
|---|---|
| Standalone `telegram_bridge.py` | Native A0 plugin at repo root |
| `python-telegram-bot` library | `aiogram` (consistent with A0 ecosystem) |
| HTTP `/api_message` calls | Direct `AgentContext` integration |
| `.env` configuration | WebUI settings panel |
| Supervisord lifecycle | A0 job_loop lifecycle |
| Single bot | Multiple bots supported |
| Polling only | Polling + webhook modes |

---

## Features

- **Text messages** -- forwarded to Agent Zero, responses streamed back
- **Photos** -- sent as file attachments for vision analysis
- **Documents/files** -- forwarded as attachments with configurable size limit
- **Rich Markdown rendering** -- tables (as monospace), code blocks, LaTeX stripping
- **Long message splitting** -- auto-splits responses exceeding Telegram's 4096 char limit
- **Multi-level send fallback** -- HTML -> plain text -> truncated
- **Agent control** -- pause, resume, nudge stuck agents
- **Context window info** -- check token usage
- **Task scheduler** -- list and trigger scheduled tasks with inline buttons
- **Project switching** -- switch active A0 project per Telegram user
- **Inline keyboards** -- agent can send interactive buttons
- **Per-user chat sessions** -- each Telegram user gets a dedicated AgentContext
- **State persistence** -- remembers contexts across restarts
- **Access control** -- per-bot allow-list by user ID, @username, or chat ID
- **Group support** -- mention, all, or off modes
- **Typing indicator** -- persistent "typing..." while agent processes
- **WebUI config** -- full settings panel in Agent Zero WebUI
- **Auto dependency install** -- aiogram installed on first use via uv
- **Multiple bots** -- run as many bots as you need

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Start the bot |
| `/help` | Show available commands |
| `/clear` | Start a new conversation |
| `/status` | Show connection status |
| `/id` | Show your User/Chat ID |
| `/stop` | Pause the agent (stop current work) |
| `/resume` | Resume a paused agent |
| `/nudge` | Kick the agent when stuck |
| `/context` | Show context window info |
| `/tasks` | List scheduled tasks (with Run buttons) |
| `/project` | List and switch A0 projects |

---

## Quick Install

### Via Agent Zero Plugin Manager

Once published to the [a0-plugins](https://github.com/agent0ai/a0-plugins) index, YATCA will be installable directly from the Agent Zero WebUI plugin browser.

### Manual Install

Clone this repo into your Agent Zero plugins directory:

```bash
cd /path/to/agent-zero/plugins
git clone https://github.com/mirecekd/yatca.git yatca
```

Then:

1. Open the Agent Zero WebUI
2. Go to Settings -> External
3. Find "YATCA" and click Configure
4. Add a bot with your Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
5. Enable the bot and save

The bot will start automatically. No restart needed.

> **Tip:** Don't know your Telegram User ID? Start the bot without access control, send `/id`, then add your ID to the allowed users list.

---

## Plugin Structure

```text
yatca/                           # Repo root = plugin root
|-- plugin.yaml                  # Plugin metadata
|-- default_config.yaml          # Default configuration
|-- requirements.txt             # Python dependencies (aiogram, aiohttp)
|-- README.md                    # This file
|
|-- helpers/
|   |-- __init__.py
|   |-- constants.py             # Plugin name, paths, context keys
|   |-- dependencies.py          # Auto-install aiogram via uv
|   |-- telegram_client.py       # Telegram API wrapper, MD->HTML converter
|   |-- bot_manager.py           # Bot lifecycle, polling/webhook
|   +-- handler.py               # All commands, message routing, A0 API
|
|-- extensions/python/
|   |-- job_loop/
|   |   +-- _10_yatca_bot.py     # Bot lifecycle manager
|   |-- system_prompt/
|   |   +-- _20_yatca_context.py # Telegram system prompt injection
|   |-- tool_execute_after/
|   |   +-- _50_yatca_response.py # Response tool intercept
|   +-- process_chain_end/
|       +-- _55_yatca_reply.py   # Auto-send final reply
|
|-- prompts/                     # Prompt templates
|-- api/                         # Webhook + test_connection endpoints
|-- webui/                       # Settings panel (config.html + store.js)
|
|-- telegram_bridge.py           # Legacy v1 standalone bridge
|-- yatca_run.sh                 # Legacy v1 runner script
+-- yatca_startup.sh             # Legacy v1 installer script
```

---

## Configuration Reference

All settings are configured via the WebUI. Per-bot options:

| Setting | Default | Description |
|---------|---------|-------------|
| `name` | -- | Unique bot identifier |
| `enabled` | `true` | Enable/disable the bot |
| `token` | -- | Bot token from @BotFather |
| `mode` | `polling` | `polling` or `webhook` |
| `webhook_url` | -- | Your A0 base URL (webhook mode only) |
| `webhook_secret` | -- | Optional shared secret for webhook |
| `allowed_users` | `[]` | User IDs or @usernames (empty = all) |
| `allowed_chats` | `[]` | Chat IDs (empty = all) |
| `group_mode` | `mention` | `mention`, `all`, or `off` |
| `default_project` | -- | Default A0 project name |
| `user_projects` | `{}` | Map user_id to project name |
| `max_file_size_mb` | `20` | Max attachment size in MB |
| `a0_timeout` | `300` | Request timeout in seconds |
| `attachment_max_age_hours` | `0` | Auto-cleanup age (0 = keep forever) |
| `notify_messages` | `false` | WebUI notifications for messages |
| `welcome_enabled` | `false` | Welcome message in groups |
| `welcome_message` | -- | Welcome template (`{name}` placeholder) |
| `agent_instructions` | -- | Extra instructions for the agent |

---

## Architecture

```text
+-------------+         +-------------------+         +-------------+
|  Telegram   |  HTTPS  |   YATCA Plugin    |  A0 API | Agent Zero  |
|   (User)    |<------->| (aiogram bot)     |<------->|   Engine    |
+-------------+         +-------------------+         +-------------+
                                |
                                |--- AgentContext (per-user sessions)
                                |--- /pause, /nudge  (CSRF session)
                                |--- /ctx_window_get (CSRF session)
                                |--- /scheduler_*    (CSRF session)
                                +--- /projects       (CSRF session)
```

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

- [agent0ai/agent-zero](https://github.com/agent0ai/agent-zero) -- Agent Zero plugin system and `_telegram_integration` reference plugin
- [winboost/agent-zero-telegram-bridge](https://github.com/winboost/agent-zero-telegram-bridge) -- the original Telegram bridge for Agent Zero
- [seqis/Agent Zero to Telegram Bridge](https://gist.github.com/seqis/69ba87a3d8c552b94b8a6bf9612b1c28) -- how-to guide for building an A0 Telegram bridge

---
