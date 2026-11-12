"""Kiro-Claw entry point."""

import asyncio
import logging
import re

from aiohttp import web

from .queue import ChatQueue
from .bot import create_bot
from .runner import run_in_container
from .scheduler import scheduler_loop
from .ipc import ipc_loop
from .webhook import create_webhook_app, WEBHOOK_PORT
from .events import event_processor_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

# httpx logs every request URL at INFO, and Telegram Bot API URLs embed the
# bot token (/bot<id>:<secret>/...). Keep those loggers quiet...
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


class RedactTokenFilter(logging.Filter):
    """...and mask any bot token that still reaches a log line (e.g. in errors)."""

    _pattern = re.compile(r"bot\d+:[A-Za-z0-9_-]{20,}")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if self._pattern.search(msg):
            record.msg = self._pattern.sub("bot<redacted>", msg)
            record.args = None
        return True


for _handler in logging.getLogger().handlers:
    _handler.addFilter(RedactTokenFilter())


def main():
    queue = ChatQueue(run_in_container)
    app = create_bot(queue)

    async def post_init(application):
        bot = application.bot

        async def send_fn(chat_id: int, text: str, parse_mode: str = None):
            MAX = 4096
            chunks = [text[i:i + MAX] for i in range(0, len(text), MAX)]
            for chunk in chunks:
                for mode in ([parse_mode] if parse_mode else ["MarkdownV2", "Markdown", None]):
                    try:
                        await bot.send_message(chat_id, chunk, parse_mode=mode)
                        break
                    except Exception:
                        continue

        async def send_photo_fn(chat_id: int, photo_path: str, caption: str = ""):
            try:
                with open(photo_path, "rb") as f:
                    await bot.send_photo(chat_id, photo=f, caption=caption or None)
            except Exception as e:
                logging.error("Failed to send photo %s: %s", photo_path, e)

        asyncio.create_task(scheduler_loop(send_fn))
        asyncio.create_task(ipc_loop(send_fn, send_photo_fn))
        asyncio.create_task(event_processor_loop(send_fn))

        # Start webhook server
        webhook_app = create_webhook_app()
        runner = web.AppRunner(webhook_app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
        await site.start()
        logging.info("Webhook server on port %d, scheduler, IPC, event processor started", WEBHOOK_PORT)

    app.post_init = post_init
    logging.info("Kiro-Claw starting — JARVIS Telegram bridge online")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
