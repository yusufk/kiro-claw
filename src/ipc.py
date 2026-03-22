"""IPC watcher — polls a directory for JSON messages from the container.

The container can write JSON files to /workspace/ipc/ (mounted from data/ipc/).
This enables two capabilities:
  1. Proactive messaging: container sends messages to Telegram unprompted
  2. Task scheduling: container creates/cancels scheduled tasks
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from .config import ALLOWED_CHAT_IDS
from .scheduler import schedule_task
from .db import delete_task

log = logging.getLogger(__name__)

IPC_DIR = Path(__file__).parent.parent / "data" / "ipc"
POLL_INTERVAL = 2  # seconds


def _is_allowed(chat_id: int) -> bool:
    return not ALLOWED_CHAT_IDS or chat_id in ALLOWED_CHAT_IDS


async def _process_file(filepath: Path, send_fn):
    """Process a single IPC JSON file."""
    try:
        data = json.loads(filepath.read_text())
        msg_type = data.get("type")

        if msg_type == "message":
            chat_id = data.get("chat_id")
            text = data.get("text")
            if chat_id and text and _is_allowed(int(chat_id)):
                await send_fn(int(chat_id), text)
                log.info("IPC message sent to %s", chat_id)
            else:
                log.warning("IPC message blocked — invalid or unauthorized chat_id: %s", chat_id)

        elif msg_type == "schedule_task":
            chat_id = int(data["chat_id"])
            if _is_allowed(chat_id):
                task_id = schedule_task(
                    chat_id=chat_id,
                    prompt=data["prompt"],
                    schedule_type=data["schedule_type"],
                    schedule_value=data["schedule_value"],
                )
                log.info("IPC task created: %s", task_id)

        elif msg_type == "cancel_task":
            if delete_task(data["task_id"]):
                log.info("IPC task cancelled: %s", data["task_id"])

        else:
            log.warning("Unknown IPC type: %s", msg_type)

    except Exception as e:
        log.error("IPC processing error for %s: %s", filepath.name, e)
        # Move to errors dir
        err_dir = IPC_DIR / "errors"
        err_dir.mkdir(parents=True, exist_ok=True)
        filepath.rename(err_dir / filepath.name)
        return

    filepath.unlink(missing_ok=True)


async def ipc_loop(send_fn):
    """Poll IPC directory for JSON files from the container."""
    IPC_DIR.mkdir(parents=True, exist_ok=True)
    log.info("IPC watcher started: %s", IPC_DIR)

    while True:
        try:
            for f in sorted(IPC_DIR.glob("*.json")):
                # Skip files still being written (modified < 1s ago)
                if time.time() - f.stat().st_mtime < 1:
                    continue
                await _process_file(f, send_fn)
        except Exception as e:
            log.error("IPC watcher error: %s", e)
        await asyncio.sleep(POLL_INTERVAL)
