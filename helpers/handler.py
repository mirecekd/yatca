"""
YATCA Handler - Central message routing, context lifecycle, user auth,
attachment download, reply sending, typing indicator, and all YATCA commands.

Preserves the full YATCA command set:
  /start, /help, /clear, /status, /id, /stop, /resume, /nudge,
  /context, /tasks, /project

Uses the A0 plugin system's AgentContext for per-user sessions and
direct Python API calls for control commands (no HTTP/CSRF needed).
"""

import json
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager, suppress

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    Message as TgMessage,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from agent import AgentContext, UserMessage
from helpers import plugins, files, projects
from helpers import message_queue as mq
from helpers.notification import NotificationManager, NotificationType, NotificationPriority
from helpers.persist_chat import save_tmp_chat
from helpers.print_style import PrintStyle
from helpers.errors import format_error
from initialize import initialize_agent

from usr.plugins.yatca.helpers import telegram_client as tc
from usr.plugins.yatca.helpers.bot_manager import get_bot
from usr.plugins.yatca.helpers.constants import (
    PLUGIN_NAME,
    DOWNLOAD_FOLDER,
    STATE_FILE,
    CTX_TG_BOT,
    CTX_TG_BOT_CFG,
    CTX_TG_CHAT_ID,
    CTX_TG_USER_ID,
    CTX_TG_USERNAME,
    CTX_TG_TYPING_STOP,
    CTX_TG_REPLY_TO,
    CTX_TG_ATTACHMENTS,
    CTX_TG_KEYBOARD,
    CTX_TG_PROJECT,
)


# ---------------------------------------------------------------------------
#  State persistence
# ---------------------------------------------------------------------------

_chat_map_lock = threading.Lock()


def _load_state() -> dict:
    path = files.get_abs_path(STATE_FILE)
    if os.path.isfile(path):
        try:
            return json.loads(files.read_file(path))
        except Exception:
            return {}
    return {}


def _save_state(state: dict):
    path = files.get_abs_path(STATE_FILE)
    files.make_dirs(path)
    files.write_file(path, json.dumps(state, indent=2))


def _map_key(bot_name: str, user_id: int, chat_id: int) -> str:
    return f"{bot_name}:{user_id}:{chat_id}"


# ---------------------------------------------------------------------------
#  Direct A0 Python API helpers (no HTTP/CSRF needed - we run inside A0)
# ---------------------------------------------------------------------------

def _a0_pause_context(ctx: AgentContext, paused: bool):
    """Pause or unpause an agent context directly."""
    ctx.paused = paused


def _a0_nudge_context(ctx: AgentContext):
    """Nudge (reset process chain) for a stuck agent context."""
    ctx.reset_process()


def _a0_get_context_window(ctx: AgentContext) -> dict:
    """Get context window info directly from the agent's stored data."""
    try:
        agent = ctx.streaming_agent or ctx.agent0
        window = agent.get_data(agent.DATA_NAME_CTX_WINDOW)
        if window and isinstance(window, dict):
            return {
                "tokens": window.get("tokens", 0),
                "content": window.get("text", ""),
            }
        # No window yet (fresh context, agent hasn't processed any message)
        return {"tokens": 0, "content": "(no context window yet)"}
    except Exception as e:
        PrintStyle.error(f"YATCA: failed to read context window: {e}")
        return {"tokens": 0, "content": ""}


def _a0_list_tasks() -> list[dict]:
    """List scheduled tasks directly from A0 TaskScheduler."""
    try:
        from helpers.task_scheduler import TaskScheduler
        scheduler = TaskScheduler.get()
        return scheduler.serialize_all_tasks()
    except Exception as e:
        PrintStyle.error(f"YATCA: failed to list tasks: {e}")
        return []


def _a0_run_task(task_id: str) -> dict:
    """Run a scheduled task directly."""
    try:
        from helpers.task_scheduler import TaskScheduler
        scheduler = TaskScheduler.get()
        task = scheduler.get_task(task_id)
        if not task:
            return {"success": False, "error": f"Task {task_id} not found"}
        scheduler.run_task(task)
        return {"success": True, "message": "Task started."}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _a0_list_projects() -> list[dict]:
    """List A0 projects directly."""
    try:
        project_list = projects.get_active_projects_list()
        result = []
        for p in project_list:
            if isinstance(p, dict):
                result.append(p)
            else:
                result.append({"name": str(p), "title": str(p)})
        return result
    except Exception as e:
        PrintStyle.error(f"YATCA: failed to list projects: {e}")
        return []


# ---------------------------------------------------------------------------
#  Attachment cleanup
# ---------------------------------------------------------------------------

def cleanup_old_attachments():
    """Remove downloaded attachment files older than per-bot max age."""
    config = plugins.get_plugin_config(PLUGIN_NAME) or {}
    bots_cfg = config.get("bots") or []
    total_removed = 0
    upload_dir = files.get_abs_path(DOWNLOAD_FOLDER)
    if not os.path.isdir(upload_dir):
        return
    for bot_cfg in bots_cfg:
        bot_name = bot_cfg.get("name", "")
        if not bot_name:
            continue
        max_age_hours = bot_cfg.get("attachment_max_age_hours", 0)
        if not max_age_hours or max_age_hours <= 0:
            continue
        prefix = f"yatca_{bot_name}_"
        cutoff = time.time() - max_age_hours * 3600
        for name in os.listdir(upload_dir):
            if not name.startswith(prefix):
                continue
            path = os.path.join(upload_dir, name)
            try:
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    total_removed += 1
            except OSError:
                pass
    if total_removed:
        PrintStyle.info(f"YATCA: cleaned up {total_removed} old attachment(s)")


# ---------------------------------------------------------------------------
#  Access control
# ---------------------------------------------------------------------------

def _is_allowed(bot_cfg: dict, user_id: int, username: str | None, chat_id: int) -> bool:
    """Check if user/chat is authorized. Empty lists = allow all."""
    # Check chat whitelist
    allowed_chats = bot_cfg.get("allowed_chats") or []
    if allowed_chats:
        if str(chat_id) not in [str(c).strip() for c in allowed_chats]:
            return False

    # Check user whitelist
    allowed_users = bot_cfg.get("allowed_users") or []
    if not allowed_users:
        return True
    for entry in allowed_users:
        entry_str = str(entry).strip()
        if entry_str.startswith("@"):
            if username and f"@{username}" == entry_str:
                return True
        else:
            try:
                if int(entry_str) == user_id:
                    return True
            except ValueError:
                if username and entry_str.lower() == username.lower():
                    return True
    PrintStyle.warning(f"YATCA: blocked unauthorized user {user_id} in chat {chat_id}")
    return False


def _get_project(bot_cfg: dict, user_id: int) -> str:
    user_projects = bot_cfg.get("user_projects") or {}
    project = user_projects.get(str(user_id), "")
    if not project:
        project = bot_cfg.get("default_project", "")
    return project


# ---------------------------------------------------------------------------
#  YATCA Help Text
# ---------------------------------------------------------------------------

HELP_TEXT = (
    "<b>YATCA</b>\n"
    "<i>Yet Another Telegram Connector for Agent-zero</i>\n\n"
    "Send me any message and I'll forward it to Agent Zero.\n"
    "Supported: text, photos, documents/files.\n\n"
    "<b>Commands:</b>\n"
    "/start -- Start the bot\n"
    "/help -- Show this message\n"
    "/clear -- Start a new conversation\n"
    "/status -- Show connection status\n"
    "/id -- Show your User/Chat ID\n"
    "/stop -- Pause the agent (stop current work)\n"
    "/resume -- Resume a paused agent\n"
    "/nudge -- Kick the agent when stuck\n"
    "/context -- Show context window info\n"
    "/tasks -- List scheduled tasks\n"
    "/project -- Switch A0 project"
)


# ---------------------------------------------------------------------------
#  Command handlers
# ---------------------------------------------------------------------------

async def handle_start(message: TgMessage, bot_name: str, bot_cfg: dict):
    """Handle /start command."""
    user = message.from_user
    if not user:
        return
    if not _is_allowed(bot_cfg, user.id, user.username, message.chat.id):
        await message.reply("You are not authorized to use this bot.")
        return

    instance = get_bot(bot_name)
    if not instance:
        return

    await _send_with_temp_bot(instance.bot.token, message.chat.id, HELP_TEXT)
    await _get_or_create_context(bot_name, bot_cfg, message)


async def handle_help(message: TgMessage, bot_name: str, bot_cfg: dict):
    """Handle /help command."""
    user = message.from_user
    if not user:
        return
    if not _is_allowed(bot_cfg, user.id, user.username, message.chat.id):
        return

    instance = get_bot(bot_name)
    if not instance:
        return

    await _send_with_temp_bot(instance.bot.token, message.chat.id, HELP_TEXT)


async def handle_clear(message: TgMessage, bot_name: str, bot_cfg: dict):
    """Handle /clear command -- reset user's chat context."""
    user = message.from_user
    if not user:
        return
    if not _is_allowed(bot_cfg, user.id, user.username, message.chat.id):
        return

    key = _map_key(bot_name, user.id, message.chat.id)
    with _chat_map_lock:
        state = _load_state()
        ctx_id = state.get("chats", {}).get(key)
        if ctx_id:
            ctx = AgentContext.get(ctx_id)
            if ctx:
                ctx.reset()
                PrintStyle.info(f"YATCA ({bot_name}): cleared chat for user {user.id}")

    instance = get_bot(bot_name)
    if instance:
        await _send_with_temp_bot(
            instance.bot.token, message.chat.id,
            "Chat cleared. Send a new message to start fresh.",
            parse_mode=None,
        )

    if bot_cfg.get("notify_messages", False):
        username_str = f"@{user.username}" if user.username else str(user.id)
        NotificationManager.send_notification(
            type=NotificationType.INFO,
            priority=NotificationPriority.NORMAL,
            title="YATCA: chat cleared",
            message=f"{username_str} cleared their chat via /clear",
            display_time=5,
            group="yatca",
        )


async def handle_status(message: TgMessage, bot_name: str, bot_cfg: dict):
    """Handle /status command -- show connection info."""
    user = message.from_user
    if not user:
        return
    if not _is_allowed(bot_cfg, user.id, user.username, message.chat.id):
        return

    key = _map_key(bot_name, user.id, message.chat.id)
    state = _load_state()
    ctx_id = state.get("chats", {}).get(key, "(none)")
    max_file = bot_cfg.get("max_file_size_mb", 20)
    timeout = bot_cfg.get("a0_timeout", 300)
    mode = bot_cfg.get("mode", "polling")

    # Get project info
    project = "(default)"
    with _chat_map_lock:
        chats = state.get("chats", {})
        cid = chats.get(key)
        if cid:
            ctx = AgentContext.get(cid)
            if ctx:
                project = ctx.data.get(CTX_TG_PROJECT, "(default)")

    instance = get_bot(bot_name)
    if instance:
        await _send_with_temp_bot(
            instance.bot.token, message.chat.id,
            f"<b>YATCA Status</b>\n"
            f"- Bot: <code>{bot_name}</code>\n"
            f"- Mode: <code>{mode}</code>\n"
            f"- Context: <code>{ctx_id}</code>\n"
            f"- Project: <code>{project}</code>\n"
            f"- Timeout: {timeout}s\n"
            f"- Max file: {max_file} MB",
        )


async def handle_id(message: TgMessage, bot_name: str, bot_cfg: dict):
    """Handle /id command -- show user/chat IDs."""
    user = message.from_user
    if not user:
        return
    if not _is_allowed(bot_cfg, user.id, user.username, message.chat.id):
        return

    instance = get_bot(bot_name)
    if instance:
        await _send_with_temp_bot(
            instance.bot.token, message.chat.id,
            f"<b>Your IDs</b>\n"
            f"- User ID: <code>{user.id}</code>\n"
            f"- Chat ID: <code>{message.chat.id}</code>\n"
            f"- Username: @{user.username or '(none)'}",
        )


async def handle_stop(message: TgMessage, bot_name: str, bot_cfg: dict):
    """Handle /stop command -- pause the agent."""
    user = message.from_user
    if not user:
        return
    if not _is_allowed(bot_cfg, user.id, user.username, message.chat.id):
        return

    ctx = _get_existing_context(bot_name, user.id, message.chat.id)
    if not ctx:
        await _reply_no_context(bot_name, message)
        return

    try:
        _a0_pause_context(ctx, True)
        instance = get_bot(bot_name)
        if instance:
            await _send_with_temp_bot(
                instance.bot.token, message.chat.id,
                "<b>Agent paused.</b>\n\n"
                "The agent has been stopped mid-work.\n"
                "Use /resume to continue or /clear to start fresh.",
            )
        PrintStyle.info(f"YATCA ({bot_name}): agent paused for user {user.id}")
    except Exception as e:
        PrintStyle.error(f"YATCA: failed to pause agent: {e}")
        instance = get_bot(bot_name)
        if instance:
            await _send_with_temp_bot(
                instance.bot.token, message.chat.id,
                f"Failed to stop agent: {str(e)[:300]}",
                parse_mode=None,
            )


async def handle_resume(message: TgMessage, bot_name: str, bot_cfg: dict):
    """Handle /resume command -- resume a paused agent."""
    user = message.from_user
    if not user:
        return
    if not _is_allowed(bot_cfg, user.id, user.username, message.chat.id):
        return

    ctx = _get_existing_context(bot_name, user.id, message.chat.id)
    if not ctx:
        await _reply_no_context(bot_name, message)
        return

    try:
        _a0_pause_context(ctx, False)
        instance = get_bot(bot_name)
        if instance:
            await _send_with_temp_bot(
                instance.bot.token, message.chat.id,
                "<b>Agent resumed.</b> Send your next message.",
            )
        PrintStyle.info(f"YATCA ({bot_name}): agent resumed for user {user.id}")
    except Exception as e:
        PrintStyle.error(f"YATCA: failed to resume agent: {e}")
        instance = get_bot(bot_name)
        if instance:
            await _send_with_temp_bot(
                instance.bot.token, message.chat.id,
                f"Failed to resume agent: {str(e)[:300]}",
                parse_mode=None,
            )


async def handle_nudge(message: TgMessage, bot_name: str, bot_cfg: dict):
    """Handle /nudge command -- kick stuck agent."""
    user = message.from_user
    if not user:
        return
    if not _is_allowed(bot_cfg, user.id, user.username, message.chat.id):
        return

    ctx = _get_existing_context(bot_name, user.id, message.chat.id)
    if not ctx:
        await _reply_no_context(bot_name, message)
        return

    try:
        _a0_nudge_context(ctx)
        msg = "Agent process chain reset."
        instance = get_bot(bot_name)
        if instance:
            await _send_with_temp_bot(
                instance.bot.token, message.chat.id,
                f"<b>Agent nudged!</b>\n{msg}",
            )
        PrintStyle.info(f"YATCA ({bot_name}): agent nudged for user {user.id}")
    except Exception as e:
        PrintStyle.error(f"YATCA: failed to nudge agent: {e}")
        instance = get_bot(bot_name)
        if instance:
            await _send_with_temp_bot(
                instance.bot.token, message.chat.id,
                f"Failed to nudge agent: {str(e)[:300]}",
                parse_mode=None,
            )


async def handle_context(message: TgMessage, bot_name: str, bot_cfg: dict):
    """Handle /context command -- show context window info."""
    user = message.from_user
    if not user:
        return
    if not _is_allowed(bot_cfg, user.id, user.username, message.chat.id):
        return

    ctx = _get_existing_context(bot_name, user.id, message.chat.id)
    if not ctx:
        await _reply_no_context(bot_name, message)
        return

    try:
        data = _a0_get_context_window(ctx)
        tokens_used = data.get("tokens", 0)
        content_len = len(data.get("content", ""))
        tokens_fmt = f"{tokens_used:,}"
        instance = get_bot(bot_name)
        if instance:
            await _send_with_temp_bot(
                instance.bot.token, message.chat.id,
                f"<b>Context Window</b>\n\n"
                f"- Tokens: <code>{tokens_fmt}</code>\n"
                f"- Content length: <code>{content_len:,}</code> chars\n"
                f"- Context ID: <code>{ctx.id[:16]}...</code>",
            )
    except Exception as e:
        PrintStyle.error(f"YATCA: failed to get context info: {e}")
        instance = get_bot(bot_name)
        if instance:
            await _send_with_temp_bot(
                instance.bot.token, message.chat.id,
                f"Failed to get context info: {str(e)[:300]}",
                parse_mode=None,
            )


async def handle_tasks(message: TgMessage, bot_name: str, bot_cfg: dict):
    """Handle /tasks command -- list scheduled tasks with run buttons."""
    user = message.from_user
    if not user:
        return
    if not _is_allowed(bot_cfg, user.id, user.username, message.chat.id):
        return

    try:
        tasks = _a0_list_tasks()
        if not tasks:
            instance = get_bot(bot_name)
            if instance:
                await _send_with_temp_bot(
                    instance.bot.token, message.chat.id,
                    "<b>Scheduled Tasks</b>\n\nNo tasks found.",
                )
            return

        state_icons = {
            "idle": "o",
            "running": ">",
            "disabled": "-",
            "error": "!",
        }
        type_icons = {
            "scheduled": "[sched]",
            "planned": "[plan]",
            "adhoc": "[adhoc]",
        }

        lines = ["<b>Scheduled Tasks</b>\n"]
        buttons = []
        for t in tasks:
            tid = t.get("uuid", "?")
            name = t.get("name", "Unnamed")
            state = t.get("state", "unknown")
            ttype = t.get("type", "unknown")
            next_run = t.get("next_run", None)
            s_icon = state_icons.get(state, "?")
            t_icon = type_icons.get(ttype, "")

            line = f"[{s_icon}] {t_icon} <b>{name}</b>"
            line += f"  <i>({ttype}/{state})</i>"
            if next_run:
                line += f"\n    Next: <code>{next_run}</code>"
            lines.append(line)

            if state in ("idle", "error"):
                buttons.append([
                    InlineKeyboardButton(
                        text=f"Run: {name[:30]}",
                        callback_data=f"yatca_task_run:{tid}",
                    )
                ])

        text = "\n".join(lines)
        reply_markup = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None

        instance = get_bot(bot_name)
        if instance:
            async with _temp_bot(instance.bot.token) as tbot:
                await tbot.send_message(
                    chat_id=message.chat.id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )

    except Exception as e:
        PrintStyle.error(f"YATCA: failed to list tasks: {e}")
        instance = get_bot(bot_name)
        if instance:
            await _send_with_temp_bot(
                instance.bot.token, message.chat.id,
                f"Failed to list tasks: {str(e)[:300]}",
                parse_mode=None,
            )


async def handle_project(message: TgMessage, bot_name: str, bot_cfg: dict):
    """Handle /project command -- list and switch A0 projects."""
    user = message.from_user
    if not user:
        return
    if not _is_allowed(bot_cfg, user.id, user.username, message.chat.id):
        return

    key = _map_key(bot_name, user.id, message.chat.id)
    ctx = _get_existing_context(bot_name, user.id, message.chat.id)
    # Read persisted project from state (survives context resets)
    current = ""
    if ctx:
        current = ctx.data.get(CTX_TG_PROJECT, "")
    if not current:
        state = _load_state()
        current = state.get("user_projects", {}).get(key, "")

    try:
        project_list = _a0_list_projects()

        if current:
            header = f"Current project: <b>{current}</b>"
        else:
            header = "Current project: <b>None (default)</b>"

        buttons = []
        for p in project_list:
            name = p.get("name", "")
            title = p.get("title", name)
            if name == current:
                label = f"[active] {title} ({name})"
            else:
                label = f"{title} ({name})"
            buttons.append([InlineKeyboardButton(
                text=label,
                callback_data=f"yatca_project_set:{name}",
            )])

        none_label = "[active] None (default)" if not current else "None (default)"
        buttons.append([InlineKeyboardButton(text=none_label, callback_data="yatca_project_set:")])

        markup = InlineKeyboardMarkup(inline_keyboard=buttons)
        instance = get_bot(bot_name)
        if instance:
            async with _temp_bot(instance.bot.token) as tbot:
                await tbot.send_message(
                    chat_id=message.chat.id,
                    text=f"<b>Project Switching</b>\n\n{header}\n\nSelect a project:",
                    parse_mode="HTML",
                    reply_markup=markup,
                )
    except Exception as e:
        PrintStyle.error(f"YATCA: failed to list projects: {e}")
        instance = get_bot(bot_name)
        if instance:
            await _send_with_temp_bot(
                instance.bot.token, message.chat.id,
                f"Failed to list projects: {str(e)[:300]}",
                parse_mode=None,
            )


# ---------------------------------------------------------------------------
#  Callback query handler (tasks run + project set)
# ---------------------------------------------------------------------------

async def handle_callback_query(query: CallbackQuery, bot_name: str, bot_cfg: dict):
    """Handle inline keyboard button presses."""
    user = query.from_user
    if not user or not query.message:
        return

    if not _is_allowed(bot_cfg, user.id, user.username, query.message.chat.id):
        await query.answer("Not authorized.")
        return

    await query.answer()

    cb_data = query.data or ""

    if cb_data.startswith("yatca_task_run:"):
        await _callback_task_run(query, bot_name, bot_cfg, cb_data)
    elif cb_data.startswith("yatca_project_set:"):
        await _callback_project_set(query, bot_name, bot_cfg, cb_data)
    else:
        # Treat unknown callback data as a user message (keyboard buttons from agent)
        text = cb_data
        if not text:
            return
        context = await _get_or_create_context_from_user(
            bot_name, bot_cfg, user.id, user.username, query.message.chat.id,
        )
        if not context:
            return
        agent = context.agent0
        user_msg = agent.read_prompt(
            "fw.yatca.user_message.md",
            sender=_format_user(user),
            body=f"[Button pressed: {text}]",
        )
        msg_id = str(uuid.uuid4())
        mq.log_user_message(context, user_msg, [], message_id=msg_id, source=" (yatca)")
        context.communicate(UserMessage(message=user_msg, id=msg_id))
        save_tmp_chat(context)


async def _callback_task_run(query: CallbackQuery, bot_name: str, bot_cfg: dict, cb_data: str):
    """Run a scheduled task from inline button."""
    task_id = cb_data.split(":", 1)[1]
    try:
        data = _a0_run_task(task_id)
        if data.get("success"):
            msg = data.get("message", "Task started.")
            await query.message.edit_text(
                query.message.text_html + f"\n\n{msg}",
                parse_mode="HTML",
            )
        else:
            err = data.get("error", "Unknown error")
            await query.message.edit_text(
                query.message.text_html + f"\n\n{err}",
                parse_mode="HTML",
            )
        PrintStyle.info(f"YATCA: task {task_id} run triggered via Telegram")
    except Exception as e:
        PrintStyle.error(f"YATCA: failed to run task {task_id}: {e}")
        try:
            await query.message.edit_text(
                query.message.text + f"\n\nError: {str(e)[:200]}",
                parse_mode="HTML",
            )
        except Exception:
            pass


async def _callback_project_set(query: CallbackQuery, bot_name: str, bot_cfg: dict, cb_data: str):
    """Switch project from inline button."""
    project_name = cb_data.split(":", 1)[1]
    user = query.from_user
    chat_id = query.message.chat.id
    key = _map_key(bot_name, user.id, chat_id)

    # Reset context so next message starts fresh with new project
    with _chat_map_lock:
        state = _load_state()
        chats = state.setdefault("chats", {})
        old_ctx_id = chats.pop(key, None)
        if old_ctx_id:
            old_ctx = AgentContext.get(old_ctx_id)
            if old_ctx:
                old_ctx.reset()
        # Store selected project in state so it persists across context resets
        user_projects = state.setdefault("user_projects", {})
        if project_name:
            user_projects[key] = project_name
        else:
            user_projects.pop(key, None)
        _save_state(state)

    if project_name:
        PrintStyle.info(f"YATCA ({bot_name}): project switched to '{project_name}' for user {user.id}")
        try:
            await query.message.edit_text(
                f"<b>Project switched to {project_name}</b>\n"
                f"Context has been reset. Next message will use this project.",
                parse_mode="HTML",
            )
        except Exception:
            pass
    else:
        PrintStyle.info(f"YATCA ({bot_name}): project deactivated for user {user.id}")
        try:
            await query.message.edit_text(
                "<b>Project deactivated (default)</b>\n"
                "Context has been reset. Next message will use no project.",
                parse_mode="HTML",
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
#  Welcome handler
# ---------------------------------------------------------------------------

async def handle_new_members(message: TgMessage, bot_name: str, bot_cfg: dict):
    """Send welcome message when new members join a group."""
    if not bot_cfg.get("welcome_enabled", False):
        return

    new_members = message.new_chat_members or []
    if not new_members:
        return

    instance = get_bot(bot_name)
    if not instance:
        return

    template = bot_cfg.get("welcome_message", "").strip()
    if not template:
        template = "Welcome, {name}!"

    for member in new_members:
        if member.is_bot:
            continue
        name = member.full_name or member.first_name or str(member.id)
        text = template.replace("{name}", name)
        await _send_with_temp_bot(instance.bot.token, message.chat.id, text, parse_mode=None)


# ---------------------------------------------------------------------------
#  Message handler (text, photos, documents)
# ---------------------------------------------------------------------------

async def handle_message(message: TgMessage, bot_name: str, bot_cfg: dict):
    """Handle incoming user message."""
    user = message.from_user
    if not user:
        return
    if not _is_allowed(bot_cfg, user.id, user.username, message.chat.id):
        return

    instance = get_bot(bot_name)
    if not instance:
        return

    # Check file size for documents
    max_file_bytes = bot_cfg.get("max_file_size_mb", 20) * 1024 * 1024
    if message.document and message.document.file_size and message.document.file_size > max_file_bytes:
        max_mb = bot_cfg.get("max_file_size_mb", 20)
        await _send_with_temp_bot(
            instance.bot.token, message.chat.id,
            f"File too large (max {max_mb} MB).",
            parse_mode=None,
        )
        return

    # Start persistent typing indicator
    typing_stop = _start_typing(instance.bot.token, message.chat.id)

    context = await _get_or_create_context(bot_name, bot_cfg, message)
    if not context:
        typing_stop.set()
        await _send_with_temp_bot(
            instance.bot.token, message.chat.id,
            "Failed to create chat session.",
            parse_mode=None,
        )
        return

    context.data[CTX_TG_TYPING_STOP] = typing_stop

    # Reply-to tracking for group chats
    reply_to_id = None
    if message.chat.type != "private" and instance.bot_info:
        if (message.reply_to_message
                and message.reply_to_message.from_user
                and message.reply_to_message.from_user.id == instance.bot_info.id):
            reply_to_id = message.message_id
    context.data[CTX_TG_REPLY_TO] = reply_to_id

    text = _extract_message_content(message)

    async with _temp_bot(instance.bot.token) as dl_bot:
        attachments = await _download_attachments(dl_bot, message, bot_name=bot_name)

    agent = context.agent0
    user_msg = agent.read_prompt(
        "fw.yatca.user_message.md",
        sender=_format_user(user),
        body=text,
    )

    msg_id = str(uuid.uuid4())
    mq.log_user_message(context, user_msg, attachments, message_id=msg_id, source=" (yatca)")
    context.communicate(UserMessage(
        message=user_msg,
        attachments=attachments,
        id=msg_id,
    ))

    save_tmp_chat(context)

    if bot_cfg.get("notify_messages", False):
        username_str = f"@{user.username}" if user.username else str(user.id)
        preview = (text[:80] + "...") if len(text) > 80 else text
        NotificationManager.send_notification(
            type=NotificationType.INFO,
            priority=NotificationPriority.HIGH,
            title="YATCA: new message",
            message=f"From {username_str}: {preview}",
            display_time=10,
            group="yatca",
        )


# ---------------------------------------------------------------------------
#  Context management
# ---------------------------------------------------------------------------

def _get_existing_context(bot_name: str, user_id: int, chat_id: int) -> AgentContext | None:
    """Get existing context without creating a new one."""
    key = _map_key(bot_name, user_id, chat_id)
    with _chat_map_lock:
        state = _load_state()
        ctx_id = state.get("chats", {}).get(key)
        if ctx_id:
            return AgentContext.get(ctx_id)
    return None


async def _reply_no_context(bot_name: str, message: TgMessage):
    """Reply that there's no active context."""
    instance = get_bot(bot_name)
    if instance:
        await _send_with_temp_bot(
            instance.bot.token, message.chat.id,
            "No active conversation. Send a message first.",
            parse_mode=None,
        )


async def _get_or_create_context(
    bot_name: str,
    bot_cfg: dict,
    message: TgMessage,
) -> AgentContext | None:
    user = message.from_user
    if not user:
        return None
    return await _get_or_create_context_from_user(
        bot_name, bot_cfg, user.id, user.username, message.chat.id,
    )


async def _get_or_create_context_from_user(
    bot_name: str,
    bot_cfg: dict,
    user_id: int,
    username: str | None,
    chat_id: int,
) -> AgentContext | None:
    key = _map_key(bot_name, user_id, chat_id)

    with _chat_map_lock:
        state = _load_state()
        chats = state.setdefault("chats", {})
        ctx_id = chats.get(key)

        if ctx_id:
            ctx = AgentContext.get(ctx_id)
            if ctx:
                return ctx
            chats.pop(key, None)

        try:
            config = initialize_agent()
            display_name = f"@{username}" if username else str(user_id)
            ctx = AgentContext(config, name=f"YATCA: {display_name}")

            ctx.data[CTX_TG_BOT] = bot_name
            ctx.data[CTX_TG_BOT_CFG] = bot_cfg
            ctx.data[CTX_TG_CHAT_ID] = chat_id
            ctx.data[CTX_TG_USER_ID] = user_id
            ctx.data[CTX_TG_USERNAME] = username or ""

            # Check persisted project from state first, then bot config
            project = _load_state().get("user_projects", {}).get(key, "")
            if not project:
                project = _get_project(bot_cfg, user_id)
            if project:
                ctx.data[CTX_TG_PROJECT] = project
                projects.activate_project(ctx.id, project)

            # Try to inherit model override from sibling context
            _inherit_model_override(ctx)

            chats[key] = ctx.id
            _save_state(state)

            PrintStyle.success(
                f"YATCA ({bot_name}): new chat {ctx.id} for user {display_name}"
            )
            return ctx

        except Exception as e:
            PrintStyle.error(f"YATCA: failed to create context: {format_error(e)}")
            return None


# ---------------------------------------------------------------------------
#  Message content extraction
# ---------------------------------------------------------------------------

def _extract_message_content(message: TgMessage) -> str:
    parts = []

    if message.text:
        parts.append(message.text)
    elif message.caption:
        parts.append(message.caption)

    if message.location:
        loc = message.location
        parts.append(f"[Location: {loc.latitude}, {loc.longitude}]")

    if message.contact:
        c = message.contact
        parts.append(f"[Contact: {c.first_name} {c.last_name or ''} phone={c.phone_number}]")

    if message.sticker:
        parts.append(f"[Sticker: {message.sticker.emoji or ''}]")

    for attr, label in [("voice", "Voice message"), ("video_note", "Video note")]:
        if getattr(message, attr, None):
            parts.append(f"[{label} -- see attachment]")

    return "\n".join(parts) if parts else "[No text content]"


async def _download_attachments(bot, message: TgMessage, bot_name: str = "") -> list[str]:
    """Download photos, documents, audio, voice, video from message."""
    paths: list[str] = []
    tg_prefix = f"yatca_{bot_name}_" if bot_name else "yatca_"
    download_dir = files.get_abs_path(DOWNLOAD_FOLDER)
    os.makedirs(download_dir, exist_ok=True)
    download_dir_ref = files.get_abs_path_dockerized(DOWNLOAD_FOLDER)

    async def _dl(file_id: str, filename: str) -> str | None:
        safe_name = f"{tg_prefix}{uuid.uuid4().hex[:8]}_{filename}"
        dest = os.path.join(download_dir, safe_name)
        result = await tc.download_file(bot, file_id, dest)
        if result:
            return os.path.join(download_dir_ref, safe_name)
        return None

    if message.photo:
        photo = message.photo[-1]
        path = await _dl(photo.file_id, f"photo_{photo.file_unique_id}.jpg")
        if path:
            paths.append(path)

    _types = [
        ("document", "file", None),
        ("audio", "audio", ".mp3"),
        ("voice", "voice", ".ogg"),
        ("video", "video", ".mp4"),
        ("video_note", "videonote", ".mp4"),
    ]
    for attr, prefix, ext in _types:
        obj = getattr(message, attr, None)
        if not obj:
            continue
        raw_name = getattr(obj, "file_name", None) or f"{prefix}_{obj.file_unique_id}{ext or ''}"
        # Sanitize: strip path components to prevent directory traversal
        fname = os.path.basename(raw_name).replace("..", "_")
        if not fname:
            fname = f"{prefix}_{obj.file_unique_id}{ext or ''}"
        path = await _dl(obj.file_id, fname)
        if path:
            paths.append(path)

    return paths


# ---------------------------------------------------------------------------
#  Reply sending (called from process_chain_end extension)
# ---------------------------------------------------------------------------

async def send_telegram_reply(
    context: AgentContext,
    response_text: str,
    attachments: list[str] | None = None,
    keyboard: list[list[dict]] | None = None,
) -> str | None:
    """Send reply to Telegram user. Returns error string or None on success."""
    bot_name = context.data.get(CTX_TG_BOT)
    if not bot_name:
        return "No YATCA bot configured on context"

    instance = get_bot(bot_name)
    if not instance:
        return f"Bot '{bot_name}' not running"

    chat_id = context.data.get(CTX_TG_CHAT_ID)
    if not chat_id:
        return "No chat_id on context"

    reply_to = context.data.get(CTX_TG_REPLY_TO)

    try:
        async with _temp_bot(instance.bot.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) as reply_bot:
            if attachments:
                for path in attachments:
                    local_path = files.fix_dev_path(path)
                    if tc.is_image_file(local_path):
                        await tc.send_photo(reply_bot, chat_id, local_path, reply_to_message_id=reply_to)
                    else:
                        await tc.send_file(reply_bot, chat_id, local_path, reply_to_message_id=reply_to)

            if response_text:
                html_text = tc.md_to_telegram_html(response_text)
                if keyboard:
                    await tc.send_text_with_keyboard(reply_bot, chat_id, html_text, keyboard, reply_to_message_id=reply_to)
                else:
                    await tc.send_text(reply_bot, chat_id, html_text, reply_to_message_id=reply_to)

        return None

    except Exception as e:
        error = format_error(e)
        PrintStyle.error(f"YATCA reply failed: {error}")
        return error


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _temp_bot(token: str, **kwargs):
    """Create a temporary Bot, yield it, and ensure the session is closed."""
    bot = Bot(token=token, **kwargs)
    try:
        yield bot
    finally:
        with suppress(Exception):
            await bot.session.close()


async def _send_with_temp_bot(token: str, chat_id: int, text: str, parse_mode: str | None = "HTML"):
    """Send text using a temporary Bot to avoid cross-event-loop session issues."""
    async with _temp_bot(token) as bot:
        await tc.send_text(bot, chat_id, text, parse_mode=parse_mode)


def _start_typing(token: str, chat_id: int) -> threading.Event:
    """Spawn a daemon thread that sends typing every 4s. Returns a stop Event."""
    stop = threading.Event()

    def _run():
        import asyncio

        async def _loop():
            async with _temp_bot(token) as bot:
                while not stop.is_set():
                    await tc.send_typing(bot, chat_id)
                    for _ in range(8):
                        if stop.is_set():
                            return
                        await asyncio.sleep(0.5)

        try:
            asyncio.run(_loop())
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()
    return stop


def _format_user(user) -> str:
    name = user.first_name or ""
    if user.last_name:
        name += f" {user.last_name}"
    if user.username:
        name += f" (@{user.username})"
    return name.strip() or str(user.id)


def _inherit_model_override(ctx: AgentContext):
    """Copy chat_model_override from the most recent sibling context in the same project."""
    project = ctx.get_data("project")
    if not project:
        return
    try:
        from plugins._model_config.helpers.model_config import is_chat_override_allowed
        if not is_chat_override_allowed(ctx.agent0):
            return
    except Exception:
        return
    source = max(
        (c for c in AgentContext.all()
         if c.id != ctx.id and c.get_data("project") == project and c.get_data("chat_model_override")),
        key=lambda c: c.last_message,
        default=None,
    )
    if source:
        ctx.set_data("chat_model_override", source.get_data("chat_model_override"))
