"""Telegram bot — receives messages, dispatches to container, IPC-only responses."""

import asyncio
import logging
import re
import time

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from .config import TELEGRAM_BOT_TOKEN, TRIGGER_PATTERN, ALLOWED_CHAT_IDS
from .queue import ChatQueue
from .runner import stream_from_container
from .db import get_tasks_for_chat, delete_task, store_message

log = logging.getLogger(__name__)

TG_MAX_MSG = 4096
_MDV2_ESCAPE = re.compile(r'([_*\[\]()~`>#+\-=|{}.!\\])')


def _escape_mdv2(text: str) -> str:
    """Escape text for MarkdownV2, preserving existing formatting."""
    return _MDV2_ESCAPE.sub(r'\\\1', text)


async def _send_smart(bot, chat_id: int, text: str):
    """Send with MarkdownV2, fall back to Markdown, then plain text."""
    for mode in ("MarkdownV2", "Markdown", None):
        try:
            await bot.send_message(chat_id, text, parse_mode=mode)
            return
        except Exception:
            continue


def _split_message(text: str) -> list[str]:
    if len(text) <= TG_MAX_MSG:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:TG_MAX_MSG])
        text = text[TG_MAX_MSG:]
    return chunks


def _is_allowed(chat_id: int) -> bool:
    return not ALLOWED_CHAT_IDS or chat_id in ALLOWED_CHAT_IDS


def _should_respond(text: str, is_private: bool) -> str | None:
    """Return the prompt if we should respond, None otherwise."""
    if is_private:
        return text
    pattern = re.escape(TRIGGER_PATTERN)
    match = re.match(rf"^{pattern}\s*(.*)", text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else None


def create_bot(queue: ChatQueue) -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    async def cmd_ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("FRIDAY online, Boss.")

    async def cmd_chatid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(f"Chat ID: `{update.effective_chat.id}`", parse_mode="Markdown")

    async def cmd_tasks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _is_allowed(update.effective_chat.id):
            return
        tasks = get_tasks_for_chat(update.effective_chat.id)
        if not tasks:
            await update.message.reply_text("No active tasks.")
            return
        lines = []
        for t in tasks:
            lines.append(f"• `{t['id']}` [{t['schedule_type']}] — {t['prompt'][:60]}\n  Next: {t['next_run']}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not _is_allowed(update.effective_chat.id):
            return
        task_id = update.message.text.partition(" ")[2].strip()
        if not task_id:
            await update.message.reply_text("Usage: /cancel <task_id>")
            return
        if delete_task(task_id):
            await update.message.reply_text(f"✅ Task `{task_id}` cancelled.", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"Task `{task_id}` not found.", parse_mode="Markdown")

    OWNER_ID = 72911340

    async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        msg = update.message
        if not msg:
            return

        chat_id = msg.chat_id
        if not _is_allowed(chat_id):
            log.info("Rejected message from chat %s", chat_id)
            return

        is_private = msg.chat.type == "private"
        is_group = msg.chat.type in ("group", "supergroup")
        text = msg.text or msg.caption or ""

        if is_group:
            sender_name = msg.from_user.first_name if msg.from_user else "Unknown"
            sender_id = msg.from_user.id if msg.from_user else 0
            is_bot = msg.from_user.is_bot if msg.from_user else False
            ts = msg.date.isoformat() if msg.date else ""
            store_message(chat_id, sender_name, sender_id, text, ts)
            if is_bot:
                # Bot-to-bot: HA notifications, images, events — store as context
                log.info("[BOT MSG] %s: %s", sender_name, text[:200])
                # Forward photos from HA bot to container as events
                if msg.photo:
                    log.info("[BOT PHOTO] %s sent a photo", sender_name)
                return
            if sender_id != OWNER_ID:
                log.info("[GROUP OBSERVE] %s: %s", sender_name, text[:200])
                return

        if not text:
            return

        prompt = _should_respond(text, is_private)
        if not prompt:
            return

        sender = msg.from_user.first_name if msg.from_user else "Someone"
        full_prompt = f"[Telegram from {sender}]: {prompt}"

        ts = msg.date.isoformat() if msg.date else ""
        if not is_group:
            store_message(chat_id, sender, msg.from_user.id if msg.from_user else 0, text, ts)

        log.info("Processing message from %s in chat %s", sender, chat_id)

        # Typing indicator while container processes — IPC delivers the response
        typing_active = True
        async def _typing_loop():
            while typing_active:
                try:
                    await ctx.bot.send_chat_action(chat_id, "typing")
                except Exception:
                    pass
                await asyncio.sleep(4)

        typing_task = asyncio.create_task(_typing_loop())

        # Collect container output and send to Telegram
        lines = []
        async for line in stream_from_container(full_prompt, chat_id):
            lines.append(line)

        typing_active = False
        typing_task.cancel()

        response = "\n".join(lines).strip()
        if response:
            for chunk in _split_message(response):
                try:
                    await ctx.bot.send_message(chat_id, chunk, parse_mode="Markdown")
                except Exception:
                    try:
                        await ctx.bot.send_message(chat_id, chunk)
                    except Exception:
                        pass

    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("chatid", cmd_chatid))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.CAPTION) & ~filters.COMMAND, handle_message))

    return app
