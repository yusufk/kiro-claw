"""Kiro-Claw entry point."""

import asyncio
import logging

from .queue import ChatQueue
from .bot import create_bot
from .runner import run_in_container
from .scheduler import scheduler_loop
from .ipc import ipc_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


def main():
    queue = ChatQueue(run_in_container)
    app = create_bot(queue)

    # We need the bot instance to send proactive messages.
    # python-telegram-bot's run_polling() manages its own event loop,
    # so we hook into post_init to start our background tasks.
    async def post_init(application):
        bot = application.bot

        async def send_fn(chat_id: int, text: str):
            MAX = 4096
            chunks = [text[i:i + MAX] for i in range(0, len(text), MAX)]
            for chunk in chunks:
                try:
                    await bot.send_message(chat_id, chunk, parse_mode="Markdown")
                except Exception:
                    await bot.send_message(chat_id, chunk)

        asyncio.create_task(scheduler_loop(send_fn))
        asyncio.create_task(ipc_loop(send_fn))
        logging.info("Scheduler and IPC watcher started")

    app.post_init = post_init
    logging.info("Kiro-Claw starting — JARVIS Telegram bridge online")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
