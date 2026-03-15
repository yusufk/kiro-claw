"""Kiro-Claw entry point."""

import logging
from .runner import run_in_container
from .queue import ChatQueue
from .bot import create_bot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


def main():
    queue = ChatQueue(run_in_container)
    app = create_bot(queue)
    logging.info("Kiro-Claw starting — JARVIS Telegram bridge online")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
