"""Telegram bot — receives messages, dispatches to queue, returns responses."""

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
DRAFT_INTERVAL = 1.0  # min seconds between draft updates


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

    async def cmd_tasks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """List active tasks: /tasks"""
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
        """Cancel a task: /cancel <task_id>"""
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

    # User ID for the owner (only respond to this user in groups)
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

        # In groups: observe all messages, only respond to owner
        if is_group:
            sender_name = msg.from_user.first_name if msg.from_user else "Unknown"
            sender_id = msg.from_user.id if msg.from_user else 0
            ts = msg.date.isoformat() if msg.date else ""
            store_message(chat_id, sender_name, sender_id, text, ts)
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

        # Store incoming message
        ts = msg.date.isoformat() if msg.date else ""
        if not is_group:  # group messages already stored above
            store_message(chat_id, sender, msg.from_user.id if msg.from_user else 0, text, ts)

        log.info("Processing message from %s in chat %s", sender, chat_id)

        # Send typing indicator while container spins up and processes
        typing_active = True
        async def _typing_loop():
            while typing_active:
                try:
                    await ctx.bot.send_chat_action(chat_id, "typing")
                except Exception:
                    pass
                await asyncio.sleep(4)

        typing_task = asyncio.create_task(_typing_loop())

        draft_id = int(time.time() * 1000) % (2**31 - 1) or 1
        accumulated = ""
        last_draft = 0.0

        async for line in stream_from_container(full_prompt, chat_id):
            accumulated = (accumulated + "\n" + line).strip() if accumulated else line
            now = time.monotonic()
            if is_private and now - last_draft >= DRAFT_INTERVAL:
                try:
                    draft_text = accumulated[-TG_MAX_MSG:]
                    await ctx.bot.send_message_draft(chat_id, draft_id, draft_text, parse_mode="Markdown")
                    last_draft = now
                except Exception as e:
                    log.debug("Draft send failed: %s", e)

        typing_active = False
        typing_task.cancel()

        if not accumulated:
            accumulated = "No response from container."

        # Suppress "ok" / acknowledgment-only responses (JARVIS already sent via IPC)
        clean = accumulated.strip().lower().rstrip(".")
        if clean in ("ok", "acknowledged", "noted", "done", "silent", ""):
            from datetime import datetime, timezone
            store_message(chat_id, "JARVIS", 0, accumulated, datetime.now(timezone.utc).isoformat(), is_bot=True)
            return

        for chunk in _split_message(accumulated):
            try:
                await msg.reply_text(chunk, parse_mode="Markdown")
            except Exception:
                await msg.reply_text(chunk)

        # Store bot response
        from datetime import datetime, timezone
        store_message(chat_id, "JARVIS", 0, accumulated, datetime.now(timezone.utc).isoformat(), is_bot=True)

    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("chatid", cmd_chatid))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.CAPTION) & ~filters.COMMAND, handle_message))

    return app
