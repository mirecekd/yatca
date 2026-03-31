"""
YATCA Telegram client helpers.
Low-level Telegram API wrapper: send text/file/photo, Markdown->HTML converter,
keyboard builder, message splitting. Preserves YATCA's rich formatting support
including tables, LaTeX stripping, and multi-level fallback sending.
"""

import os
import re
import html as html_module

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from helpers.errors import format_error
from helpers.print_style import PrintStyle

_UNSET = object()  # sentinel: "not provided" (lets Bot default apply)

MAX_MESSAGE_LENGTH: int = 4096


# ---------------------------------------------------------------------------
#  Text messages
# ---------------------------------------------------------------------------

async def send_text(
    bot: Bot,
    chat_id: int,
    text: str,
    reply_to_message_id: int | None = None,
    parse_mode: object = _UNSET,
) -> int | None:
    """Send text message, splitting if too long. Returns last message_id or None on error.

    parse_mode behaviour:
      - _UNSET (default): omitted from send_message -> Bot's DefaultBotProperties applies.
      - None: explicitly no formatting.
      - "HTML"/"Markdown"/etc.: that specific mode.
    """
    try:
        chunks = split_message(text, MAX_MESSAGE_LENGTH)
        last_msg_id = None
        pm_kwargs: dict = {} if parse_mode is _UNSET else {"parse_mode": parse_mode}
        for chunk in chunks:
            try:
                msg = await bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    reply_to_message_id=reply_to_message_id,
                    **pm_kwargs,
                )
                last_msg_id = msg.message_id
            except TelegramBadRequest:
                # Retry as plain text, stripping HTML tags
                plain = strip_html_tags(chunk)
                msg = await bot.send_message(
                    chat_id=chat_id,
                    text=plain,
                    reply_to_message_id=reply_to_message_id,
                    parse_mode=None,
                )
                last_msg_id = msg.message_id
        return last_msg_id
    except Exception as e:
        PrintStyle.error(f"YATCA send_text failed: {format_error(e)}")
        return None


async def send_text_with_keyboard(
    bot: Bot,
    chat_id: int,
    text: str,
    buttons: list[list[dict]],
    reply_to_message_id: int | None = None,
    parse_mode: object = _UNSET,
) -> int | None:
    """Send text with inline keyboard buttons."""
    try:
        keyboard = build_inline_keyboard(buttons)
        pm_kwargs: dict = {} if parse_mode is _UNSET else {"parse_mode": parse_mode}
        msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            reply_to_message_id=reply_to_message_id,
            **pm_kwargs,
        )
        return msg.message_id
    except Exception as e:
        PrintStyle.error(f"YATCA send_text_with_keyboard failed: {format_error(e)}")
        return None


# ---------------------------------------------------------------------------
#  Files and images
# ---------------------------------------------------------------------------

async def send_file(
    bot: Bot,
    chat_id: int,
    file_path: str,
    caption: str = "",
    reply_to_message_id: int | None = None,
) -> int | None:
    """Send a file from local path. Returns message_id or None on error."""
    try:
        if not os.path.isfile(file_path):
            PrintStyle.error(f"YATCA: file not found: {file_path}")
            return None
        input_file = FSInputFile(file_path)
        msg = await bot.send_document(
            chat_id=chat_id,
            document=input_file,
            caption=caption[:1024] if caption else None,
            reply_to_message_id=reply_to_message_id,
        )
        return msg.message_id
    except Exception as e:
        PrintStyle.error(f"YATCA send_file failed: {format_error(e)}")
        return None


async def send_photo(
    bot: Bot,
    chat_id: int,
    photo_path: str,
    caption: str = "",
    reply_to_message_id: int | None = None,
) -> int | None:
    """Send a photo from local path. Returns message_id or None on error."""
    try:
        if not os.path.isfile(photo_path):
            PrintStyle.error(f"YATCA: photo not found: {photo_path}")
            return None
        input_file = FSInputFile(photo_path)
        msg = await bot.send_photo(
            chat_id=chat_id,
            photo=input_file,
            caption=caption[:1024] if caption else None,
            reply_to_message_id=reply_to_message_id,
        )
        return msg.message_id
    except Exception as e:
        PrintStyle.error(f"YATCA send_photo failed: {format_error(e)}")
        return None


# ---------------------------------------------------------------------------
#  Inline keyboards
# ---------------------------------------------------------------------------

def build_inline_keyboard(
    buttons: list[list[dict]],
) -> InlineKeyboardMarkup:
    """Build inline keyboard from a list of rows.
    Each row is a list of dicts with keys: text, callback_data or url.
    """
    rows = []
    for row in buttons:
        row_buttons = []
        for btn in row:
            if "url" in btn:
                row_buttons.append(InlineKeyboardButton(
                    text=btn["text"], url=btn["url"],
                ))
            else:
                row_buttons.append(InlineKeyboardButton(
                    text=btn["text"],
                    callback_data=btn.get("callback_data", btn["text"]),
                ))
        rows.append(row_buttons)
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
#  Typing indicator
# ---------------------------------------------------------------------------

async def send_typing(bot: Bot, chat_id: int):
    """Send 'typing...' action to chat."""
    try:
        await bot.send_chat_action(chat_id=chat_id, action="typing")
    except Exception:
        pass


# ---------------------------------------------------------------------------
#  File download
# ---------------------------------------------------------------------------

async def download_file(
    bot: Bot,
    file_id: str,
    destination: str,
) -> str | None:
    """Download a file by file_id to destination path. Returns path or None on error."""
    try:
        file = await bot.get_file(file_id)
        if not file.file_path:
            return None
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        await bot.download_file(file.file_path, destination)
        return destination
    except Exception as e:
        PrintStyle.error(f"YATCA download failed: {format_error(e)}")
        return None


# ---------------------------------------------------------------------------
#  Message splitting
# ---------------------------------------------------------------------------

def split_message(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Split text into chunks that fit Telegram's message length limit."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split_pos = text.rfind("\n", 0, limit)
        if split_pos == -1 or split_pos < limit // 2:
            split_pos = text.rfind(" ", 0, limit)
        if split_pos == -1:
            split_pos = limit
        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip("\n")
    return chunks


# ---------------------------------------------------------------------------
#  HTML helpers
# ---------------------------------------------------------------------------

def strip_html_tags(text: str) -> str:
    """Remove HTML tags from text, preserving content."""
    clean = re.sub(r"<[^>]+>", "", text)
    return html_module.unescape(clean)


_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def is_image_file(path: str) -> bool:
    _, ext = os.path.splitext(path.lower())
    return ext in _IMAGE_EXTENSIONS


# ---------------------------------------------------------------------------
#  Markdown -> Telegram HTML conversion (YATCA's rich converter)
#  Supports: code blocks, inline code, tables (as <pre>), bold, italic,
#  strikethrough, links, images, headings, horizontal rules, LaTeX stripping.
# ---------------------------------------------------------------------------

def md_to_telegram_html(text: str) -> str:
    """Convert Markdown to Telegram-compatible HTML.
    This is YATCA's enhanced converter that supports tables (rendered as
    monospace <pre> blocks), LaTeX stripping, and image indicators.
    """
    code_blocks: list[str] = []
    inline_codes: list[str] = []
    table_blocks: list[str] = []

    # -- Step 1: Stash code blocks and inline code --

    def save_code_block(m):
        code_blocks.append(m.group(2))
        return f"CODEBLOCK{len(code_blocks) - 1}"

    def save_inline_code(m):
        inline_codes.append(m.group(1))
        return f"INLINECODE{len(inline_codes) - 1}"

    text = re.sub(r"```(\w*)?\n?(.*?)```", save_code_block, text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", save_inline_code, text)

    # -- Step 2: Convert tables to monospace blocks --

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
            fmt_lines.append(" | ".join(parts))
            if ri == 0:
                sep_parts = ["-" * w for w in col_widths]
                fmt_lines.append("-+-".join(sep_parts))
        result = "\n".join(fmt_lines)
        table_blocks.append(result)
        return f"TABLEBLOCK{len(table_blocks) - 1}"

    text = re.sub(r"(?:^\|.+\|$\n?)+", convert_table, text, flags=re.MULTILINE)

    # -- Step 3: Escape HTML entities --

    text = html_module.escape(text)

    # -- Step 4: Inline formatting --

    # Headings -> bold
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    # Bold+italic
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", text)
    text = re.sub(r"___(.+?)___", r"<b><i>\1</i></b>", text)
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    # Italic
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", text)
    # Strikethrough
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    # Links
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    # Image references
    text = re.sub(r"!\[([^\]]*)\]\(img:///([^)]+)\)", r"[image: \1 \2]", text)
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"[image: \1 \2]", text)
    # Horizontal rules
    text = re.sub(r"^[-*_]{3,}$", "---", text, flags=re.MULTILINE)
    # LaTeX stripping
    text = re.sub(r"<latex>(.*?)</latex>", r"\1", text)

    # -- Step 5: Restore stashed blocks --

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
