"""Task scheduler — polls for due tasks and runs them through the container."""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone, timedelta

from croniter import croniter

from .db import (
    create_task,
    get_due_tasks,
    update_after_run,
    log_run,
    delete_task,
    get_tasks_for_chat,
    get_task,
    cancel_task,
    count_recent_runs,
)
from .runner import run_in_container

log = logging.getLogger(__name__)

POLL_INTERVAL = 30  # seconds

# --- Runaway-loop guard ---
# Minimum sane interval for repeating tasks. Anything shorter is clamped up —
# the container takes ~10-55s per run, so sub-minute intervals never make sense.
MIN_INTERVAL_MS = 5 * 60 * 1000  # 5 minutes
# If a task runs more than this many times inside the window, it's looping —
# auto-disable it rather than let it hammer the container indefinitely.
RUNAWAY_MAX_RUNS = 12
RUNAWAY_WINDOW_SECONDS = 3 * 60 * 60  # 3 hours


def _to_sqlite_utc(ts: str) -> str:
    """Normalize any ISO timestamp to SQLite-compatible 'YYYY-MM-DD HH:MM:SS' UTC."""
    ts = ts.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _next_run(schedule_type: str, schedule_value: str) -> str | None:
    now = datetime.now(timezone.utc)
    if schedule_type == "cron":
        return croniter(schedule_value, now).get_next(datetime).strftime("%Y-%m-%d %H:%M:%S")
    elif schedule_type == "interval":
        ms = int(schedule_value)
        return (now + timedelta(milliseconds=ms)).strftime("%Y-%m-%d %H:%M:%S")
    return None  # 'once' — no next run


def schedule_task(chat_id: int, prompt: str, schedule_type: str, schedule_value: str) -> str:
    """Create a scheduled task. Returns task ID."""
    task_id = f"task-{uuid.uuid4().hex[:8]}"

    # Clamp absurdly short intervals — the container needs ~10-55s per run, so
    # anything under MIN_INTERVAL_MS just backlogs the scheduler and burns cycles.
    if schedule_type == "interval":
        try:
            ms = int(schedule_value)
            if ms < MIN_INTERVAL_MS:
                log.warning(
                    "Interval %dms for task is below floor; clamping to %dms",
                    ms, MIN_INTERVAL_MS,
                )
                schedule_value = str(MIN_INTERVAL_MS)
        except (ValueError, TypeError):
            pass

    if schedule_type == "once":
        # Validate it's a real timestamp, not a bare number
        try:
            next_run = _to_sqlite_utc(schedule_value)
        except (ValueError, TypeError):
            # Treat bare numbers as minutes
            try:
                minutes = int(schedule_value)
                next_run = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
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
    # Runaway-loop guard: if a repeating task has fired too many times recently,
    # it's almost certainly a mis-scheduled no-op loop (see the 2026-10-24
    # night-alarm-watch incident). Auto-disable and tell Boss once.
    if task["schedule_type"] in ("interval", "cron"):
        recent = count_recent_runs(task["id"], RUNAWAY_WINDOW_SECONDS)
        if recent >= RUNAWAY_MAX_RUNS:
            cancel_task(task["id"])
            log.error(
                "Runaway task %s auto-disabled: %d runs in %dh. Prompt: %s",
                task["id"], recent, RUNAWAY_WINDOW_SECONDS // 3600, task["prompt"][:80],
            )
            try:
                await send_fn(
                    task["chat_id"],
                    f"⚠️ Auto-disabled a runaway scheduled task ({task['id']}): "
                    f"it fired {recent} times in {RUNAWAY_WINDOW_SECONDS // 3600}h. "
                    f"Likely a mis-scheduled loop, not a real alert. Stopping it.",
                )
            except Exception:
                pass
            return

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
                # Re-check status right before running — a cancel_task that landed
                # during this batch (e.g. via IPC) must be honored, closing the
                # long-standing "I tried to cancel it but it ran anyway" race.
                current = get_task(task["id"])
                if not current or current["status"] != "active":
                    log.info("Skipping %s — no longer active (%s)",
                             task["id"], current["status"] if current else "deleted")
                    continue
                await _run_task(task, send_fn)
        except Exception as e:
            log.error("Scheduler error: %s", e)
        await asyncio.sleep(POLL_INTERVAL)
