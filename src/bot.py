"""Telegram bot — receives messages, dispatches to queue, returns responses."""

import asyncio
import logging
import re
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

log = logging.getLogger(__name__)

TG_MAX_MSG = 4096


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
        await update.message.reply_text("JARVIS online, Sir.")

    async def cmd_chatid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(f"Chat ID: `{update.effective_chat.id}`", parse_mode="Markdown")

    async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        msg = update.message
        if not msg or not msg.text:
            return

        chat_id = msg.chat_id
        if not _is_allowed(chat_id):
            return

        is_private = msg.chat.type == "private"
        prompt = _should_respond(msg.text, is_private)
        if not prompt:
            return

        sender = msg.from_user.first_name if msg.from_user else "Someone"
        full_prompt = f"[Telegram from {sender}]: {prompt}"

        log.info("Processing message from %s in chat %s", sender, chat_id)

        async def typing_loop():
            while True:
                await msg.chat.send_action("typing")
                await asyncio.sleep(4)

        typing_task = asyncio.create_task(typing_loop())
        try:
            response = await queue.submit(full_prompt, chat_id)
        finally:
            typing_task.cancel()

        for chunk in _split_message(response):
            await msg.reply_text(chunk)

    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("chatid", cmd_chatid))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app
