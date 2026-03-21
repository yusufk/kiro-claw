"""Telegram bot — receives messages, dispatches to queue, returns responses."""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone, timedelta

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
from .scheduler import schedule_task
from .db import get_tasks_for_chat, delete_task

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


def _parse_remind(text: str) -> tuple[str, str, str] | None:
    """Parse /remind command. Returns (prompt, schedule_type, schedule_value) or None.

    Formats:
      /remind 30m Check the server         -> interval, 1800000
      /remind 2h Water the plants           -> interval, 7200000
      /remind 2026-03-22T10:00 Do the thing -> once, ISO timestamp
      /remind cron 0 9 * * * Morning report -> cron, expression
    """
    text = text.strip()

    # Cron: /remind cron <expr> <prompt>
    m = re.match(r"cron\s+((?:\S+\s+){4}\S+)\s+(.+)", text, re.DOTALL)
    if m:
        return m.group(2).strip(), "cron", m.group(1).strip()

    # Interval: /remind 30m|2h|90s <prompt>
    m = re.match(r"(\d+)([smhd])\s+(.+)", text, re.DOTALL)
    if m:
        val, unit, prompt = int(m.group(1)), m.group(2), m.group(3).strip()
        ms = val * {"s": 1000, "m": 60000, "h": 3600000, "d": 86400000}[unit]
        return prompt, "interval", str(ms)

    # Once: /remind <ISO datetime> <prompt>
    m = re.match(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?)\s+(.+)", text, re.DOTALL)
    if m:
        return m.group(2).strip(), "once", m.group(1).replace(" ", "T")

    # Simple delay: /remind <minutes> <prompt>  (bare number = minutes)
    m = re.match(r"(\d+)\s+(.+)", text, re.DOTALL)
    if m:
        minutes = int(m.group(1))
        when = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
        return m.group(2).strip(), "once", when

    return None


def create_bot(queue: ChatQueue) -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    async def cmd_ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("JARVIS online, Sir.")

    async def cmd_chatid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(f"Chat ID: `{update.effective_chat.id}`", parse_mode="Markdown")

    async def cmd_remind(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Schedule a task: /remind 30m Check the server"""
        if not _is_allowed(update.effective_chat.id):
            return
        text = update.message.text.partition(" ")[2].strip()
        if not text:
            await update.message.reply_text(
                "Usage:\n"
                "  /remind 30m Check server\n"
                "  /remind 2h Water plants\n"
                "  /remind 2026-03-22T10:00 Meeting\n"
                "  /remind cron 0 9 * * * Daily report\n"
                "  /remind 15 Quick reminder (minutes)"
            )
            return

        parsed = _parse_remind(text)
        if not parsed:
            await update.message.reply_text("Couldn't parse that. Try: /remind 30m Do something")
            return

        prompt, stype, svalue = parsed
        task_id = schedule_task(update.effective_chat.id, prompt, stype, svalue)
        label = {"cron": f"cron `{svalue}`", "interval": f"every {text.split()[0]}", "once": f"at {svalue}"}
        await update.message.reply_text(f"✅ Scheduled ({label.get(stype, stype)})\nTask: `{task_id}`\nPrompt: {prompt}", parse_mode="Markdown")

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

        draft_id = int(time.time() * 1000) % (2**31 - 1) or 1
        accumulated = ""
        last_draft = 0.0

        async for line in stream_from_container(full_prompt, chat_id):
            accumulated = (accumulated + "\n" + line).strip() if accumulated else line
            now = time.monotonic()
            if is_private and now - last_draft >= DRAFT_INTERVAL:
                try:
                    draft_text = accumulated[-TG_MAX_MSG:]  # keep within limit
                    await ctx.bot.send_message_draft(chat_id, draft_id, draft_text)
                    last_draft = now
                except Exception as e:
                    log.debug("Draft send failed: %s", e)

        if not accumulated:
            accumulated = "No response from container."

        for chunk in _split_message(accumulated):
            await msg.reply_text(chunk)

    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("chatid", cmd_chatid))
    app.add_handler(CommandHandler("remind", cmd_remind))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app
