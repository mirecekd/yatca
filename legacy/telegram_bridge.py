"""
YATCA — Yet Another Telegram Connector for Agent-zero
Bridges Telegram messages to Agent Zero's /api_message HTTP API.

Supported: text messages, photos, documents/files.

Usage:
    docker exec -it agent-zero /opt/venv/bin/python3 /a0/usr/workdir/telegram_bridge.py

Requirements (inside container):
    /opt/venv/bin/pip install aiohttp python-telegram-bot python-dotenv
"""

import sys
import os
import asyncio
import base64
import logging
import traceback
import re
import json
import html as html_module

sys.path.insert(0, "/a0")

import aiohttp
from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv("/a0/usr/.env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
A0_API_URL = os.getenv("A0_API_URL", "http://127.0.0.1:80/api_message")
A0_TIMEOUT = int(os.getenv("A0_TIMEOUT", "300"))
ALLOWED_CHATS = os.getenv("TELEGRAM_CHAT_IDS", "")
ALLOWED_CHAT_SET = set(ALLOWED_CHATS.split(",")) if ALLOWED_CHATS.strip() else set()
ALLOWED_USERS = os.getenv("TELEGRAM_USER_IDS", "")
ALLOWED_USER_SET = set(ALLOWED_USERS.split(",")) if ALLOWED_USERS.strip() else set()
TELEGRAM_MAX_LEN = 4096
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE_MB", "20")) * 1024 * 1024
YATCA_STATE_FILE = os.getenv("YATCA_STATE_FILE", "/a0/usr/workdir/yatca_state.json")

# Derive A0 API base URL from A0_API_URL (strip /api_message suffix)
A0_API_BASE = A0_API_URL.rsplit("/", 1)[0] if "/api_message" in A0_API_URL else A0_API_URL.rstrip("/")

# A0 Web UI credentials for internal API calls (CSRF-protected endpoints)
A0_AUTH_LOGIN = os.getenv("AUTH_LOGIN", "")
A0_AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "")


def get_a0_api_key() -> str:
    """Read API key fresh - survives A0 restarts without bridge restart."""
    env_key = os.getenv("A0_API_KEY", "")
    if env_key:
        return env_key
    try:
        from python.helpers.settings import get_settings
        token = get_settings().get("mcp_server_token", "")
        if token:
            return token
    except Exception as e:
        print(f"[WARN] Could not auto-discover API key: {e}")
    return ""


A0_API_KEY = get_a0_api_key()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("telegram_bridge")

chat_contexts: dict[str, str] = {}
chat_projects: dict[str, str] = {}  # chat_id -> project_name

def save_state() -> None:
    """Persist chat_contexts and chat_projects to disk (atomic write)."""
    state = {
        "chat_contexts": chat_contexts,
        "chat_projects": chat_projects,
    }
    tmp_path = YATCA_STATE_FILE + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, YATCA_STATE_FILE)
        log.debug(f"State saved ({len(chat_contexts)} contexts, {len(chat_projects)} projects)")
    except Exception as e:
        log.error(f"Failed to save state: {e}")


def load_state() -> None:
    """Load persisted state from disk into chat_contexts and chat_projects."""
    if not os.path.exists(YATCA_STATE_FILE):
        log.info("No state file found, starting fresh.")
        return
    try:
        with open(YATCA_STATE_FILE, "r") as f:
            state = json.load(f)
        chat_contexts.update(state.get("chat_contexts", {}))
        chat_projects.update(state.get("chat_projects", {}))
        log.info(
            f"State loaded: {len(chat_contexts)} context(s), "
            f"{len(chat_projects)} project(s)"
        )
    except Exception as e:
        log.error(f"Failed to load state: {e}")



def is_authorized(update: Update) -> bool:
    chat_id = str(update.effective_chat.id)
    user_id = str(update.effective_user.id)
    if ALLOWED_CHAT_SET and chat_id not in ALLOWED_CHAT_SET:
        return False
    if ALLOWED_USER_SET and user_id not in ALLOWED_USER_SET:
        log.warning(f"Blocked unauthorized user {user_id} in chat {chat_id}")
        return False
    return True


def markdown_to_telegram_html(text: str) -> str:
    code_blocks = []
    inline_codes = []
    def save_code_block(m):
        code_blocks.append(m.group(2))
        return f"CODEBLOCK{len(code_blocks) - 1}"
    def save_inline_code(m):
        inline_codes.append(m.group(1))
        return f"INLINECODE{len(inline_codes) - 1}"
    text = re.sub(r"```(\w*)?\n?(.*?)```", save_code_block, text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", save_inline_code, text)
    table_blocks = []
    def convert_table(m):
        table_text = m.group(0)
        tlines = table_text.strip().split("\n")
        rows = []
        for tl in tlines:
            tl = tl.strip()
            if not tl.startswith("|"):
                continue
            if re.match(r"^\|[\s\-:|]+\|$", tl):
                continue
            cells = [c.strip() for c in tl.split("|")[1:-1]]
            rows.append(cells)
        if not rows:
            return table_text
        num_cols = max(len(r) for r in rows)
        col_widths = [0] * num_cols
        for row in rows:
            for i, cell in enumerate(row):
                if i < num_cols:
                    col_widths[i] = max(col_widths[i], len(cell))
        fmt_lines = []
        for ri, row in enumerate(rows):
            parts = []
            for i in range(num_cols):
                cell = row[i] if i < len(row) else ""
                parts.append(cell.ljust(col_widths[i]))
            fmt_lines.append(" │ ".join(parts))
            if ri == 0:
                sep_parts = ["─" * w for w in col_widths]
                fmt_lines.append("─┼─".join(sep_parts))
        result = "\n".join(fmt_lines)
        table_blocks.append(result)
        return f"TABLEBLOCK{len(table_blocks) - 1}"
    text = re.sub(r"(?:^\|.+\|$\n?)+", convert_table, text, flags=re.MULTILINE)
    text = html_module.escape(text)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", text)
    text = re.sub(r"___(.+?)___", r"<b><i>\1</i></b>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"!\[([^\]]*)\]\(img:///([^)]+)\)", r"🖼 \1 [\2]", text)
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"🖼 \1 [\2]", text)
    text = re.sub(r"^[-*_]{3,}$", "—" * 20, text, flags=re.MULTILINE)
    text = re.sub(r"<latex>(.*?)</latex>", r"\1", text)
    for i, table in enumerate(table_blocks):
        escaped_table = html_module.escape(table)
        text = text.replace(f"TABLEBLOCK{i}", f"<pre>{escaped_table}</pre>")
    for i, block in enumerate(code_blocks):
        escaped_block = html_module.escape(block)
        text = text.replace(f"CODEBLOCK{i}", f"<pre>{escaped_block}</pre>")
    for i, code in enumerate(inline_codes):
        escaped_code = html_module.escape(code)
        text = text.replace(f"INLINECODE{i}", f"<code>{escaped_code}</code>")
    return text


def split_message(text: str, limit: int = TELEGRAM_MAX_LEN) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split_pos = text.rfind("\n", 0, limit)
        if split_pos == -1:
            split_pos = text.rfind(" ", 0, limit)
        if split_pos == -1:
            split_pos = limit
        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip("\n")
    return chunks


def strip_html_tags(text: str) -> str:
    """Remove HTML tags from text, preserving content."""
    clean = re.sub(r"<[^>]+>", "", text)
    return html_module.unescape(clean)


async def safe_send_html(
    message,
    text: str,
    raw_text: str = "",
    edit: bool = False,
) -> bool:
    """Send or edit a Telegram message with 3-level fallback.

    1. Try HTML parse_mode
    2. Strip HTML tags -> send as plain text
    3. Truncate to 4000 chars as last resort

    Returns True if message was sent/edited successfully.
    """
    send = message.edit_text if edit else message.reply_text

    # Level 1: HTML
    try:
        await send(text, parse_mode="HTML")
        return True
    except Exception as e:
        log.warning(f"HTML {'edit' if edit else 'send'} failed: {e}")

    # Level 2: Strip tags -> plain text
    plain = raw_text or strip_html_tags(text)
    try:
        await send(plain)
        return True
    except Exception as e:
        log.warning(f"Plain text {'edit' if edit else 'send'} failed: {e}")

    # Level 3: Truncate
    try:
        truncated = plain[:4000] + "\n\n[...truncated]"
        await send(truncated)
        return True
    except Exception as e:
        log.error(f"All send attempts failed: {e}")
        return False


async def download_file_bytes(file_obj) -> bytes:
    tg_file = await file_obj.get_file()
    return await tg_file.download_as_bytearray()


def bytes_to_attachment(data: bytes, filename: str) -> dict:
    return {"filename": filename, "base64": base64.b64encode(data).decode("utf-8")}


class A0SessionManager:
    """Manages authenticated session with A0 web UI (login + CSRF token)."""

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None
        self._csrf_token: str = ""

    async def _ensure_session(self):
        """Create aiohttp session, login, and obtain CSRF token if needed."""
        if self._session is None or self._session.closed:
            jar = aiohttp.CookieJar(unsafe=True)  # unsafe=True for IP-based URLs
            self._session = aiohttp.ClientSession(cookie_jar=jar)
            self._csrf_token = ""

        if not self._csrf_token:
            await self._login_and_get_csrf()

    async def _login_and_get_csrf(self):
        """Authenticate with A0 web UI and obtain CSRF token."""
        base = A0_API_BASE

        # Step 1: Login with form data
        if A0_AUTH_LOGIN:
            login_data = aiohttp.FormData()
            login_data.add_field("username", A0_AUTH_LOGIN)
            login_data.add_field("password", A0_AUTH_PASSWORD)
            async with self._session.post(
                f"{base}/login", data=login_data,
                timeout=aiohttp.ClientTimeout(total=15),
                allow_redirects=False,
            ) as resp:
                if resp.status not in (200, 302):
                    text = await resp.text()
                    raise RuntimeError(f"A0 login failed HTTP {resp.status}: {text[:300]}")
                log.info("A0 session: logged in successfully")

        # Step 2: Get CSRF token
        headers = {
            "Origin": base,
            "Referer": f"{base}/",
        }
        async with self._session.get(
            f"{base}/csrf_token", headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                self._csrf_token = data.get("token", "")
                if self._csrf_token:
                    log.info(f"A0 session: CSRF token obtained ({self._csrf_token[:8]}...)")
                else:
                    raise RuntimeError("A0 CSRF token endpoint returned empty token")
            else:
                text = await resp.text()
                raise RuntimeError(f"A0 CSRF token request failed HTTP {resp.status}: {text[:300]}")

    async def api_call(self, endpoint: str, payload: dict, timeout: int = 30) -> dict:
        """Call an internal A0 API endpoint with proper auth + CSRF."""
        await self._ensure_session()
        url = f"{A0_API_BASE}/{endpoint}"
        headers = {
            "Content-Type": "application/json",
            "X-CSRF-Token": self._csrf_token,
            "X-Forwarded-For": "127.0.0.1",
            "X-Real-IP": "127.0.0.1",
        }
        async with self._session.post(
            url, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status == 403:
                # CSRF token expired or session lost — re-authenticate and retry once
                log.warning(f"A0 API {endpoint}: 403 — re-authenticating...")
                self._csrf_token = ""
                await self._ensure_session()
                headers["X-CSRF-Token"] = self._csrf_token
                async with self._session.post(
                    url, json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as retry_resp:
                    if retry_resp.status == 200:
                        return await retry_resp.json()
                    else:
                        error_text = await retry_resp.text()
                        raise RuntimeError(f"A0 API {endpoint} returned HTTP {retry_resp.status}: {error_text[:500]}")
            elif resp.status == 200:
                return await resp.json()
            else:
                error_text = await resp.text()
                raise RuntimeError(f"A0 API {endpoint} returned HTTP {resp.status}: {error_text[:500]}")

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# Global session manager instance
_a0_session = A0SessionManager()


async def a0_api_call(endpoint: str, payload: dict, timeout: int = 30) -> dict:
    """Call an internal A0 API endpoint (e.g. 'pause', 'nudge', 'scheduler_tasks_list')."""
    return await _a0_session.api_call(endpoint, payload, timeout)


async def send_to_agent(
    message_text: str,
    context_id: str = "",
    attachments: list[dict] | None = None,
    project_name: str | None = None,
) -> dict:
    """Send a message (+ optional attachments) to Agent Zero. Fresh API key every call."""
    payload: dict = {"message": message_text, "context_id": context_id}
    if project_name:
        payload["project_name"] = project_name
    if attachments:
        payload["attachments"] = attachments
    api_key = get_a0_api_key()
    headers = {
        "Content-Type": "application/json",
        "X-API-KEY": api_key,
        "X-Forwarded-For": "127.0.0.1",
        "X-Real-IP": "127.0.0.1",
    }
    timeout = aiohttp.ClientTimeout(total=A0_TIMEOUT)
    async with aiohttp.ClientSession() as session:
        async with session.post(
            A0_API_URL, json=payload, headers=headers, timeout=timeout
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            else:
                error_text = await resp.text()
                raise RuntimeError(
                    f"Agent Zero returned HTTP {resp.status}: {error_text[:500]}"
                )


async def agent_reply(
    update: Update,
    message_text: str,
    attachments: list[dict] | None = None,
    user_display: str = "",
) -> None:
    """Shared handler: send to Agent Zero, reply to user with Markdown->HTML."""
    chat_id = str(update.effective_chat.id)
    context_id = chat_contexts.get(chat_id, "")
    att_info = f" + {len(attachments)} attachment(s)" if attachments else ""
    log.info(
        f"[{user_display}] -> Agent Zero: "
        f"{message_text[:100]}{'...' if len(message_text) > 100 else ''}{att_info}"
    )
    project = chat_projects.get(chat_id)

    # Send processing indicator immediately
    processing_msg = await update.message.reply_text("⏳ Processing...")

    import time as _time
    _proc_start = _time.monotonic()
    typing_active = True

    async def keep_typing_and_update():
        """Keep typing indicator alive and update processing message with elapsed time."""
        update_interval = 10  # seconds between processing message updates
        typing_interval = 4   # seconds between typing indicators
        next_update = update_interval
        elapsed_tick = 0
        status_messages = [
            (10, "⏳ Processing... ({elapsed}s)"),
            (20, "⏳ Processing... ({elapsed}s) — taking a bit longer"),
            (30, "⏳ Processing... ({elapsed}s) — still working..."),
            (60, "⏳ Processing... ({elapsed}s) — complex task, hang tight"),
            (120, "⏳ Processing... ({elapsed}s) — this is a long one..."),
        ]
        while typing_active:
            try:
                await asyncio.sleep(typing_interval)
                if not typing_active:
                    break
                await update.effective_chat.send_action(ChatAction.TYPING)
                elapsed_tick += typing_interval
                if elapsed_tick >= next_update:
                    elapsed = int(_time.monotonic() - _proc_start)
                    # Pick the right status message
                    msg = status_messages[0][1]  # default
                    for threshold, template in status_messages:
                        if elapsed >= threshold:
                            msg = template
                    try:
                        await processing_msg.edit_text(msg.format(elapsed=elapsed))
                    except Exception:
                        pass  # edit can fail if message was already edited
                    next_update += update_interval
            except Exception:
                break

    await update.effective_chat.send_action(ChatAction.TYPING)
    typing_task = asyncio.create_task(keep_typing_and_update())
    try:
        data = await send_to_agent(message_text, context_id, attachments, project_name=project)
    except Exception:
        # On error, remove the processing indicator so callers can send their own error
        typing_active = False
        typing_task.cancel()
        try:
            await processing_msg.delete()
        except Exception:
            pass
        raise
    finally:
        typing_active = False
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass
    new_context = data.get("context_id", "")
    if new_context:
        chat_contexts[chat_id] = new_context
        save_state()
    reply = data.get("response", "") or "(Agent returned an empty response)"
    log.info(
        f"Agent Zero -> [{user_display}]: {reply[:100]}{'...' if len(reply) > 100 else ''}"
    )
    html_reply = markdown_to_telegram_html(reply)
    chunks = split_message(html_reply)
    plain_chunks = split_message(reply)

    # Edit the processing message with the first chunk (with HTML fallback)
    first_ok = await safe_send_html(
        processing_msg, chunks[0],
        raw_text=plain_chunks[0] if plain_chunks else "",
        edit=True,
    )
    if not first_ok:
        # Edit failed entirely - delete processing msg and send as new message
        try:
            await processing_msg.delete()
        except Exception:
            pass
        await safe_send_html(
            update.message, chunks[0],
            raw_text=plain_chunks[0] if plain_chunks else "",
        )

    # Send remaining chunks as new messages (with HTML fallback)
    for i, chunk in enumerate(chunks[1:], start=1):
        raw = plain_chunks[i] if i < len(plain_chunks) else ""
        await safe_send_html(update.message, chunk, raw_text=raw)


# ---------------------------------------------------------------------------
#  Command handlers
# ---------------------------------------------------------------------------

HELP_TEXT = (
    "🦙 <b>Welcome to YATCA</b>\n"
    "<i>Yet Another Telegram Connector for Agent-zero</i>\n\n"
    "Send me any message and I'll forward it to Agent Zero.\n"
    "Supported: text, photos, documents/files.\n\n"
    "<b>Commands:</b>\n"
    "/start — Start the bot\n"
    "/help — Show this message\n"
    "/reset — Start a new conversation\n"
    "/status — Show connection status\n"
    "/id — Show your User/Chat ID\n"
    "/stop — Pause the agent (stop current work)\n"
    "/resume — Resume a paused agent\n"
    "/nudge — Kick the agent when stuck\n"
    "/context — Show context window info\n"
    "/tasks — List scheduled tasks\n"
    "/project — Switch A0 project"
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    await update.message.reply_text(HELP_TEXT, parse_mode="HTML")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    chat_id = str(update.effective_chat.id)
    chat_contexts.pop(chat_id, None)
    save_state()
    await update.message.reply_text("🔄 Conversation reset. Starting fresh.")
    log.info(f"Context reset for chat {chat_id}")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    chat_id = str(update.effective_chat.id)
    ctx = chat_contexts.get(chat_id, "(none)")
    await update.message.reply_text(
        f"🦙 <b>Bot Status</b>\n"
        f"• API: <code>{A0_API_URL}</code>\n"
        f"• Context: <code>{ctx}</code>\n"
        f"• Timeout: {A0_TIMEOUT}s\n"
        f"• Max file: {MAX_FILE_SIZE // 1024 // 1024} MB",
        parse_mode="HTML",
    )


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    user = update.effective_user
    chat = update.effective_chat
    await update.message.reply_text(
        f"🆔 <b>Your IDs</b>\n"
        f"• User ID: <code>{user.id}</code>\n"
        f"• Chat ID: <code>{chat.id}</code>\n"
        f"• Username: @{user.username or '(none)'}",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
#  P1 Commands: /stop, /resume, /nudge, /context, /tasks
# ---------------------------------------------------------------------------

async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pause the agent - stops current work."""
    if not is_authorized(update):
        return
    chat_id = str(update.effective_chat.id)
    ctx_id = chat_contexts.get(chat_id, "")
    if not ctx_id:
        await update.message.reply_text("⚠️ No active conversation to stop. Send a message first.")
        return
    try:
        data = await a0_api_call("pause", {"paused": True, "context": ctx_id})
        await update.message.reply_text(
            "⏸️ <b>Agent paused.</b>\n\n"
            "The agent has been stopped mid-work.\n"
            "Use /resume to continue or /reset to start fresh.",
            parse_mode="HTML",
        )
        log.info(f"Agent paused for chat {chat_id} (ctx: {ctx_id})")
    except Exception as e:
        log.error(f"Failed to pause agent: {e}")
        await update.message.reply_text(f"❌ Failed to stop agent: {str(e)[:300]}")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resume a paused agent."""
    if not is_authorized(update):
        return
    chat_id = str(update.effective_chat.id)
    ctx_id = chat_contexts.get(chat_id, "")
    if not ctx_id:
        await update.message.reply_text("⚠️ No active conversation to resume.")
        return
    try:
        data = await a0_api_call("pause", {"paused": False, "context": ctx_id})
        await update.message.reply_text(
            "▶️ <b>Agent resumed.</b> Send your next message.",
            parse_mode="HTML",
        )
        log.info(f"Agent resumed for chat {chat_id} (ctx: {ctx_id})")
    except Exception as e:
        log.error(f"Failed to resume agent: {e}")
        await update.message.reply_text(f"❌ Failed to resume agent: {str(e)[:300]}")


async def cmd_nudge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kick the agent when it's stuck - resets process chain."""
    if not is_authorized(update):
        return
    chat_id = str(update.effective_chat.id)
    ctx_id = chat_contexts.get(chat_id, "")
    if not ctx_id:
        await update.message.reply_text("⚠️ No active conversation to nudge. Send a message first.")
        return
    try:
        data = await a0_api_call("nudge", {"ctxid": ctx_id})
        msg = data.get("message", "Agent nudged.")
        await update.message.reply_text(
            f"🔄 <b>Agent nudged!</b>\n{msg}",
            parse_mode="HTML",
        )
        log.info(f"Agent nudged for chat {chat_id} (ctx: {ctx_id})")
    except Exception as e:
        log.error(f"Failed to nudge agent: {e}")
        await update.message.reply_text(f"❌ Failed to nudge agent: {str(e)[:300]}")


async def cmd_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show context window info - tokens, fill level."""
    if not is_authorized(update):
        return
    chat_id = str(update.effective_chat.id)
    ctx_id = chat_contexts.get(chat_id, "")
    if not ctx_id:
        await update.message.reply_text("⚠️ No active conversation. Send a message first.")
        return
    try:
        data = await a0_api_call("ctx_window_get", {"context": ctx_id})
        tokens_used = data.get("tokens", 0)
        content_len = len(data.get("content", ""))
        # Format token count with thousands separator
        tokens_fmt = f"{tokens_used:,}"
        await update.message.reply_text(
            f"📊 <b>Context Window</b>\n\n"
            f"├ Tokens: <code>{tokens_fmt}</code>\n"
            f"├ Content length: <code>{content_len:,}</code> chars\n"
            f"└ Context ID: <code>{ctx_id[:16]}...</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        log.error(f"Failed to get context info: {e}")
        await update.message.reply_text(f"❌ Failed to get context info: {str(e)[:300]}")


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List scheduled tasks with inline buttons to run them."""
    if not is_authorized(update):
        return
    try:
        data = await a0_api_call("scheduler_tasks_list", {})
        tasks = data.get("tasks", [])
        if not tasks:
            await update.message.reply_text(
                "📋 <b>Scheduled Tasks</b>\n\nNo tasks found.",
                parse_mode="HTML",
            )
            return

        # State emoji mapping
        state_icons = {
            "idle": "⚪",
            "running": "🟢",
            "disabled": "⚫",
            "error": "🔴",
        }
        type_icons = {
            "scheduled": "⏰",
            "planned": "📅",
            "adhoc": "⚡",
        }

        lines = ["📋 <b>Scheduled Tasks</b>\n"]
        buttons = []
        for t in tasks:
            tid = t.get("uuid", "?")
            name = t.get("name", "Unnamed")
            state = t.get("state", "unknown")
            ttype = t.get("type", "unknown")
            next_run = t.get("next_run", None)
            s_icon = state_icons.get(state, "❓")
            t_icon = type_icons.get(ttype, "")

            line = f"{s_icon} {t_icon} <b>{name}</b>"
            line += f"  <i>({ttype}/{state})</i>"
            if next_run:
                line += f"\n    Next: <code>{next_run}</code>"
            lines.append(line)

            # Add run button for idle/error tasks
            if state in ("idle", "error"):
                buttons.append([
                    InlineKeyboardButton(
                        f"▶ Run: {name[:30]}",
                        callback_data=f"task_run:{tid}",
                    )
                ])

        text = "\n".join(lines)
        reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)

    except Exception as e:
        log.error(f"Failed to list tasks: {e}")
        await update.message.reply_text(f"❌ Failed to list tasks: {str(e)[:300]}")


async def callback_task_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button press to run a task."""
    query = update.callback_query
    await query.answer()

    if not is_authorized(update):
        return

    cb_data = query.data or ""
    if not cb_data.startswith("task_run:"):
        return

    task_id = cb_data.split(":", 1)[1]
    try:
        await query.edit_message_text(
            query.message.text + f"\n\n⏳ <i>Running task {task_id[:8]}...</i>",
            parse_mode="HTML",
        )
        data = await a0_api_call("scheduler_task_run", {"task_id": task_id})
        if data.get("success"):
            msg = data.get("message", "Task started.")
            await query.edit_message_text(
                query.message.text_html + f"\n\n✅ {msg}",
                parse_mode="HTML",
            )
        else:
            err = data.get("error", "Unknown error")
            await query.edit_message_text(
                query.message.text_html + f"\n\n❌ {err}",
                parse_mode="HTML",
            )
        log.info(f"Task {task_id} run triggered via Telegram")
    except Exception as e:
        log.error(f"Failed to run task {task_id}: {e}")
        try:
            await query.edit_message_text(
                query.message.text + f"\n\n❌ Error: {str(e)[:200]}",
                parse_mode="HTML",
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
#  Project switching: /project command + inline keyboard callback
# ---------------------------------------------------------------------------

async def cmd_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current project and list available projects as inline keyboard."""
    if not is_authorized(update):
        return
    chat_id = str(update.effective_chat.id)
    current = chat_projects.get(chat_id)
    try:
        data = await a0_api_call("projects", {"action": "list"})
        projects = data.get("data", [])

        if current:
            header = f"Current project: <b>{current}</b>"
        else:
            header = "Current project: <b>None (default)</b>"

        buttons = []
        for p in projects:
            name = p.get("name", "")
            title = p.get("title", name)
            if name == current:
                label = f"[active] {title} ({name})"
            else:
                label = f"{title} ({name})"
            buttons.append([InlineKeyboardButton(label, callback_data=f"project_set:{name}")])

        # Add 'None (default)' button to deactivate
        none_label = "[active] None (default)" if not current else "None (default)"
        buttons.append([InlineKeyboardButton(none_label, callback_data="project_set:")])

        markup = InlineKeyboardMarkup(buttons)
        await update.message.reply_text(
            f"-- <b>Project Switching</b> --\n\n{header}\n\nSelect a project:",
            parse_mode="HTML",
            reply_markup=markup,
        )
    except Exception as e:
        log.error(f"Failed to list projects: {e}")
        await update.message.reply_text(f"Failed to list projects: {str(e)[:300]}")


async def callback_project_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button press to switch project."""
    query = update.callback_query
    await query.answer()

    if not is_authorized(update):
        return

    cb_data = query.data or ""
    if not cb_data.startswith("project_set:"):
        return

    project_name = cb_data.split(":", 1)[1]  # empty string means 'None'
    chat_id = str(update.effective_chat.id)

    # Reset current context so next message starts fresh with new project
    old_ctx = chat_contexts.pop(chat_id, None)
    save_state()

    if project_name:
        chat_projects[chat_id] = project_name
        save_state()
        log.info(f"Project switched to '{project_name}' for chat {chat_id} (context reset)")
        try:
            await query.edit_message_text(
                f"-- Project switched to <b>{project_name}</b> --\n"
                f"Context has been reset. Next message will use this project.",
                parse_mode="HTML",
            )
        except Exception:
            pass
    else:
        chat_projects.pop(chat_id, None)
        save_state()
        log.info(f"Project deactivated for chat {chat_id} (context reset)")
        try:
            await query.edit_message_text(
                "-- Project deactivated (default) --\n"
                "Context has been reset. Next message will use no project.",
                parse_mode="HTML",
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
#  Message handlers (text, photo, document)
# ---------------------------------------------------------------------------

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Forward plain text messages to Agent Zero."""
    if not update.message or not update.message.text:
        return
    if not is_authorized(update):
        return
    content = update.message.text.strip()
    if not content:
        return
    user = update.effective_user
    user_display = user.username or user.first_name or str(user.id)
    try:
        await agent_reply(update, content, user_display=user_display)
    except asyncio.TimeoutError:
        log.warning(f"Timeout waiting for Agent Zero (>{A0_TIMEOUT}s)")
        await update.message.reply_text(
            f"⏳ Agent Zero took too long to respond (timeout: {A0_TIMEOUT}s). "
            f"Try again or use /reset to start fresh."
        )
    except aiohttp.ClientConnectorError as e:
        log.error(f"Connection error: {e}")
        await update.message.reply_text(
            f"🔌 Cannot connect to Agent Zero API. Is the server running?\n"
            f"Target: <code>{A0_API_URL}</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        log.error(f"Error: {traceback.format_exc()}")
        await update.message.reply_text(f"❌ Error: {str(e)[:500]}")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Download the highest-resolution photo and forward it to Agent Zero."""
    if not is_authorized(update):
        return
    user = update.effective_user
    user_display = user.username or user.first_name or str(user.id)
    caption = update.message.caption or "What do you see in this image?"
    status_msg = await update.message.reply_text("📎 Downloading and forwarding image...")
    try:
        photo = update.message.photo[-1]
        if photo.file_size and photo.file_size > MAX_FILE_SIZE:
            await status_msg.edit_text(f"❌ Photo too large (max {MAX_FILE_SIZE // 1024 // 1024} MB).")
            return
        data = bytes(await download_file_bytes(photo))
        attachment = bytes_to_attachment(data, "photo.jpg")
        await status_msg.delete()
        await agent_reply(update, caption, attachments=[attachment], user_display=user_display)
    except Exception as e:
        log.error(f"Error handling photo: {traceback.format_exc()}")
        await status_msg.edit_text(f"❌ Failed to forward photo: {str(e)[:300]}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Download a document/file and forward it to Agent Zero."""
    if not is_authorized(update):
        return
    user = update.effective_user
    user_display = user.username or user.first_name or str(user.id)
    doc = update.message.document
    caption = update.message.caption or f"I'm sending you a file: {doc.file_name}"
    filename = doc.file_name or "document"
    if doc.file_size and doc.file_size > MAX_FILE_SIZE:
        await update.message.reply_text(f"❌ File too large (max {MAX_FILE_SIZE // 1024 // 1024} MB).")
        return
    status_msg = await update.message.reply_text(f"📎 Downloading <code>{filename}</code>...", parse_mode="HTML")
    try:
        data = bytes(await download_file_bytes(doc))
        attachment = bytes_to_attachment(data, filename)
        await status_msg.delete()
        await agent_reply(update, caption, attachments=[attachment], user_display=user_display)
    except Exception as e:
        log.error(f"Error handling document: {traceback.format_exc()}")
        await status_msg.edit_text(f"❌ Failed to forward file: {str(e)[:300]}")


# ---------------------------------------------------------------------------
#  Main entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not found in /a0/usr/.env")
        sys.exit(1)
    if not A0_API_KEY:
        print("ERROR: Could not determine Agent Zero API key.")
        print("Set A0_API_KEY in /a0/usr/.env or ensure A0 settings are accessible.")
        sys.exit(1)

    print("=" * 60)
    print("  YATCA — Yet Another Telegram Connector for Agent-zero")
    print("=" * 60)
    print(f"  API URL:   {A0_API_URL}")
    print(f"  API Base:  {A0_API_BASE}")
    print(f"  API Key:   {A0_API_KEY[:4]}****")
    print(f"  Timeout:   {A0_TIMEOUT}s")
    print(f"  Max file:  {MAX_FILE_SIZE // 1024 // 1024} MB")
    if ALLOWED_USER_SET:
        print(f"  Users:     {', '.join(sorted(ALLOWED_USER_SET))}")
    else:
        print("  Users:     (all)")
    print(f"  State:     {YATCA_STATE_FILE}")
    print("=" * 60)

    BOT_COMMANDS = [
        BotCommand("start", "Start the bot"),
        BotCommand("help", "Show available commands"),
        BotCommand("reset", "Start a new conversation"),
        BotCommand("status", "Show connection status"),
        BotCommand("id", "Show your User/Chat ID"),
        BotCommand("stop", "Pause the agent"),
        BotCommand("resume", "Resume paused agent"),
        BotCommand("nudge", "Kick stuck agent"),
        BotCommand("context", "Show context window info"),
        BotCommand("tasks", "List scheduled tasks"),
        BotCommand("project", "Switch A0 project"),
    ]

    async def post_init(application):
        await application.bot.set_my_commands(BOT_COMMANDS)
        log.info(
            f"Registered {len(BOT_COMMANDS)} bot menu commands: "
            f"{', '.join('/' + c.command for c in BOT_COMMANDS)}"
        )

    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    # Existing commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("id", cmd_id))

    # P1 commands
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("nudge", cmd_nudge))
    app.add_handler(CommandHandler("context", cmd_context))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("project", cmd_project))

    # Callback handler for inline keyboard buttons (task run, project set)
    app.add_handler(CallbackQueryHandler(callback_task_run, pattern=r"^task_run:"))
    app.add_handler(CallbackQueryHandler(callback_project_set, pattern=r"^project_set:"))

    # Message handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    load_state()
    log.info("Starting Telegram bot...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
