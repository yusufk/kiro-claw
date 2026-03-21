"""Task scheduler — polls for due tasks and runs them through the container."""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone, timedelta

from croniter import croniter

from .db import create_task, get_due_tasks, update_after_run, log_run, delete_task, get_tasks_for_chat
from .runner import run_in_container

log = logging.getLogger(__name__)

POLL_INTERVAL = 30  # seconds


def _next_run(schedule_type: str, schedule_value: str) -> str | None:
    now = datetime.now(timezone.utc)
    if schedule_type == "cron":
        return croniter(schedule_value, now).get_next(datetime).isoformat()
    elif schedule_type == "interval":
        ms = int(schedule_value)
        return (now + timedelta(milliseconds=ms)).isoformat()
    return None  # 'once' — no next run


def schedule_task(chat_id: int, prompt: str, schedule_type: str, schedule_value: str) -> str:
    """Create a scheduled task. Returns task ID."""
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    if schedule_type == "once":
        # Validate it's a real timestamp, not a bare number
        try:
            datetime.fromisoformat(schedule_value)
            next_run = schedule_value
        except (ValueError, TypeError):
            # Treat bare numbers as minutes
            try:
                minutes = int(schedule_value)
                next_run = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
            except ValueError:
                next_run = schedule_value
    else:
        next_run = _next_run(schedule_type, schedule_value)

    create_task({
        "id": task_id,
        "chat_id": chat_id,
        "prompt": prompt,
        "schedule_type": schedule_type,
        "schedule_value": schedule_value,
        "next_run": next_run,
        "status": "active",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    log.info("Task %s created: %s [%s: %s]", task_id, prompt[:50], schedule_type, schedule_value)
    return task_id


async def _run_task(task: dict, send_fn):
    """Execute a single task and send the result to Telegram."""
    start = time.monotonic()
    try:
        result = await run_in_container(task["prompt"], task["chat_id"])
        duration = int((time.monotonic() - start) * 1000)
        log_run(task["id"], datetime.now(timezone.utc).isoformat(), duration, "success", result=result[:500])

        # Send result to user
        await send_fn(task["chat_id"], result)

        # Calculate next run
        next_run = _next_run(task["schedule_type"], task["schedule_value"])
        update_after_run(task["id"], next_run, result[:200])

    except Exception as e:
        duration = int((time.monotonic() - start) * 1000)
        log_run(task["id"], datetime.now(timezone.utc).isoformat(), duration, "error", error=str(e))
        log.error("Task %s failed: %s", task["id"], e)
        # Still advance next_run so we don't retry immediately
        next_run = _next_run(task["schedule_type"], task["schedule_value"])
        update_after_run(task["id"], next_run, f"Error: {e}")


async def scheduler_loop(send_fn):
    """Main scheduler loop — polls DB for due tasks."""
    log.info("Scheduler started (poll every %ds)", POLL_INTERVAL)
    while True:
        try:
            due = get_due_tasks()
            if due:
                log.info("Found %d due task(s)", len(due))
            for task in due:
                await _run_task(task, send_fn)
        except Exception as e:
            log.error("Scheduler error: %s", e)
        await asyncio.sleep(POLL_INTERVAL)
