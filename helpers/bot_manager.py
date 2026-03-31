"""
YATCA Bot Manager.
Handles bot creation, polling/webhook lifecycle, and bot registry.
Extended from the A0 plugin pattern with YATCA's full command set.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Callable, Awaitable

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatType, ContentType
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, BotCommand

from helpers.errors import format_error
from helpers.print_style import PrintStyle


# ---------------------------------------------------------------------------
#  Data models
# ---------------------------------------------------------------------------

@dataclass
class BotInstance:
    name: str
    bot: Bot
    dispatcher: Dispatcher
    router: Router
    task: asyncio.Task | None = None
    webhook_active: bool = False
    webhook_secret: str = ""
    group_mode: str = "mention"
    bot_info: object | None = None


# ---------------------------------------------------------------------------
#  Bot registry
# ---------------------------------------------------------------------------

_bots: dict[str, BotInstance] = {}


def get_bot(name: str) -> BotInstance | None:
    return _bots.get(name)


def get_all_bots() -> dict[str, BotInstance]:
    return _bots


# ---------------------------------------------------------------------------
#  YATCA bot menu commands
# ---------------------------------------------------------------------------

YATCA_BOT_COMMANDS = [
    BotCommand(command="start", description="Start the bot"),
    BotCommand(command="help", description="Show available commands"),
    BotCommand(command="clear", description="Start a new conversation"),
    BotCommand(command="status", description="Show connection status"),
    BotCommand(command="id", description="Show your User/Chat ID"),
    BotCommand(command="stop", description="Pause the agent"),
    BotCommand(command="resume", description="Resume paused agent"),
    BotCommand(command="nudge", description="Kick stuck agent"),
    BotCommand(command="context", description="Show context window info"),
    BotCommand(command="tasks", description="List scheduled tasks"),
    BotCommand(command="project", description="Switch A0 project"),
]


# ---------------------------------------------------------------------------
#  Bot creation
# ---------------------------------------------------------------------------

def create_bot(
    name: str,
    token: str,
    on_message: Callable[..., Awaitable],
    on_command_start: Callable[..., Awaitable],
    on_command_help: Callable[..., Awaitable],
    on_command_clear: Callable[..., Awaitable],
    on_command_status: Callable[..., Awaitable],
    on_command_id: Callable[..., Awaitable],
    on_command_stop: Callable[..., Awaitable],
    on_command_resume: Callable[..., Awaitable],
    on_command_nudge: Callable[..., Awaitable],
    on_command_context: Callable[..., Awaitable],
    on_command_tasks: Callable[..., Awaitable],
    on_command_project: Callable[..., Awaitable],
    on_callback_query: Callable[..., Awaitable] | None = None,
    on_new_members: Callable[..., Awaitable] | None = None,
    group_mode: str = "mention",
) -> BotInstance:
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    router = Router()

    # Register YATCA command handlers
    router.message.register(on_command_start, CommandStart())
    router.message.register(on_command_help, Command("help"))
    router.message.register(on_command_clear, Command("clear"))
    router.message.register(on_command_status, Command("status"))
    router.message.register(on_command_id, Command("id"))
    router.message.register(on_command_stop, Command("stop"))
    router.message.register(on_command_resume, Command("resume"))
    router.message.register(on_command_nudge, Command("nudge"))
    router.message.register(on_command_context, Command("context"))
    router.message.register(on_command_tasks, Command("tasks"))
    router.message.register(on_command_project, Command("project"))

    if on_callback_query:
        router.callback_query.register(on_callback_query)

    if on_new_members:
        router.message.register(on_new_members, F.content_type == ContentType.NEW_CHAT_MEMBERS)

    # Register message handler with group filtering
    if group_mode == "off":
        router.message.register(on_message, F.chat.type == ChatType.PRIVATE)
    elif group_mode == "mention":
        router.message.register(on_message, F.chat.type == ChatType.PRIVATE)
        router.message.register(_make_group_mention_filter(on_message, bot))
    else:
        router.message.register(on_message)

    dp.include_router(router)
    instance = BotInstance(name=name, bot=bot, dispatcher=dp, router=router, group_mode=group_mode)
    _bots[name] = instance
    return instance


async def cache_bot_info(instance: BotInstance):
    """Fetch and cache bot info. Call after create_bot."""
    if not instance.bot_info:
        instance.bot_info = await instance.bot.get_me()
    return instance.bot_info


async def set_bot_commands(instance: BotInstance):
    """Register YATCA's bot menu commands with Telegram."""
    try:
        await instance.bot.set_my_commands(YATCA_BOT_COMMANDS)
        cmd_list = ", ".join(f"/{c.command}" for c in YATCA_BOT_COMMANDS)
        PrintStyle.info(f"YATCA ({instance.name}): registered bot commands: {cmd_list}")
    except Exception as e:
        PrintStyle.error(f"YATCA ({instance.name}): failed to set bot commands: {format_error(e)}")


# ---------------------------------------------------------------------------
#  Group mention filter
# ---------------------------------------------------------------------------

def _make_group_mention_filter(handler: Callable, bot: Bot):
    """Create a group message handler that only responds to mentions and replies."""

    async def _group_handler(message: Message):
        if message.chat.type == ChatType.PRIVATE:
            return
        bot_info = None
        for b in _bots.values():
            if b.bot is bot:
                bot_info = b.bot_info
                break
        if not bot_info:
            bot_info = await bot.get_me()
        bot_username = bot_info.username or ""

        # Check for reply to bot
        if message.reply_to_message and message.reply_to_message.from_user:
            if message.reply_to_message.from_user.id == bot_info.id:
                await handler(message)
                return

        # Check for @mention in text or caption
        text = message.text or message.caption or ""
        entities = message.entities or message.caption_entities or []

        if text and f"@{bot_username}" in text:
            await handler(message)
            return

        for entity in entities:
            if entity.type == "mention":
                mention_text = text[entity.offset:entity.offset + entity.length]
                if mention_text.lower() == f"@{bot_username.lower()}":
                    await handler(message)
                    return

    _group_handler.__name__ = f"_group_handler_{id(handler)}"
    return _group_handler


# ---------------------------------------------------------------------------
#  Polling
# ---------------------------------------------------------------------------

async def start_polling(instance: BotInstance) -> asyncio.Task:
    try:
        await instance.bot.delete_webhook()
    except Exception:
        pass

    async def _poll():
        try:
            PrintStyle.info(f"YATCA ({instance.name}): starting polling")
            await instance.dispatcher.start_polling(
                instance.bot,
                handle_signals=False,
            )
        except asyncio.CancelledError:
            PrintStyle.info(f"YATCA ({instance.name}): polling cancelled")
        except Exception as e:
            PrintStyle.error(f"YATCA ({instance.name}): polling error: {format_error(e)}")

    task = asyncio.create_task(_poll())
    instance.task = task
    return task


async def stop_polling(instance: BotInstance):
    if instance.task and not instance.task.done():
        await instance.dispatcher.stop_polling()
        instance.task.cancel()
        try:
            await instance.task
        except asyncio.CancelledError:
            pass
    instance.task = None


# ---------------------------------------------------------------------------
#  Webhook
# ---------------------------------------------------------------------------

async def setup_webhook(instance: BotInstance, webhook_url: str, secret: str = ""):
    """Register webhook with Telegram."""
    full_url = f"{webhook_url.rstrip('/')}/api/plugins/yatca/webhook?bot={instance.name}"
    await instance.bot.set_webhook(url=full_url, secret_token=secret or None)
    instance.webhook_active = True
    instance.webhook_secret = secret
    PrintStyle.info(f"YATCA ({instance.name}): webhook active via {webhook_url.rstrip('/')}")


async def remove_webhook(instance: BotInstance):
    try:
        await instance.bot.delete_webhook()
    except Exception as e:
        PrintStyle.error(f"YATCA ({instance.name}): remove webhook error: {format_error(e)}")
    instance.webhook_active = False
    instance.webhook_secret = ""


# ---------------------------------------------------------------------------
#  Cleanup
# ---------------------------------------------------------------------------

async def stop_bot(name: str):
    instance = _bots.pop(name, None)
    if not instance:
        return
    if instance.task and not instance.task.done():
        await stop_polling(instance)
    else:
        await remove_webhook(instance)
    try:
        await instance.bot.session.close()
    except Exception:
        pass
    PrintStyle.info(f"YATCA ({name}): stopped")


# ---------------------------------------------------------------------------
#  Test connection
# ---------------------------------------------------------------------------

async def test_token(token: str) -> tuple[bool, str]:
    try:
        bot = Bot(token=token)
        info = await bot.get_me()
        await bot.session.close()
        return True, f"Connected as @{info.username} ({info.first_name})"
    except Exception as e:
        return False, format_error(e)
