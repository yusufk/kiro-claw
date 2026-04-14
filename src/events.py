"""Event processor — polls events table and routes to JARVIS container.

Output is IPC-only: JARVIS uses jarvis-send/jarvis-photo to communicate.
The container's stdout response is logged but NEVER forwarded to Telegram.
This cleanly separates the event pipeline from the chat pipeline.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from .db import get_unprocessed_events, mark_event_processed, get_events_since
from .runner import run_in_container

log = logging.getLogger(__name__)

POLL_INTERVAL = 5
DEFAULT_CHAT_ID = 72911340
BATCH_WINDOW = 10


def _is_alarm_event(event: dict) -> str | None:
    """Return 'armed' or 'disarmed' if this is an alarm state change, else None."""
    try:
        data = json.loads(event["data"]) if isinstance(event["data"], str) else event["data"]
    except (json.JSONDecodeError, TypeError):
        return None
    friendly = data.get("friendly_name", "")
    if "Alarm - armed" in friendly:
        return "armed"
    if "Alarm - disarmed" in friendly:
        return "disarmed"
    return None


def _build_briefing(alarm_state: str, event_ts: str) -> str:
    """Build a briefing prompt with event summary since last transition."""
    # Look back 12h for overnight (morning) or daytime (evening) summary
    try:
        ts = datetime.fromisoformat(event_ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        ts = datetime.now(timezone.utc)
    since = (ts - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
    recent = get_events_since(since)

    # Summarise events by type
    counts = {}
    for e in recent:
        try:
            d = json.loads(e["data"]) if isinstance(e["data"], str) else e["data"]
        except (json.JSONDecodeError, TypeError):
            d = {}
        name = d.get("friendly_name", e.get("source", "unknown"))
        counts[name] = counts.get(name, 0) + 1

    summary_lines = [f"  - {name}: {n}x" for name, n in sorted(counts.items(), key=lambda x: -x[1])]
    summary = "\n".join(summary_lines) if summary_lines else "  No events recorded."

    if alarm_state == "disarmed":
        period = "MORNING"
        context = "Alarm just disarmed (morning routine). Summarise the overnight period."
    else:
        period = "EVENING"
        context = "Alarm just armed (night routine). Summarise the daytime period."

    return (
        f"[SYSTEM EVENT — BRIEFING REQUEST]\n"
        f"{context}\n\n"
        f"Events in the last 12 hours:\n{summary}\n\n"
        f"Generate a concise {period} BRIEFING for Yusuf using jarvis-send.\n"
        f"Include: event summary, anything unusual, current DEFCON level (check your memory/knowledge graph).\n"
        f"Keep it to 3-5 lines max. Use jarvis-send to deliver it."
    )


def _summarise_events(events: list[dict]) -> str:
    """Build a prompt from a batch of events."""
    lines = []
    for e in events:
        try:
            data = json.loads(e["data"]) if isinstance(e["data"], str) else e["data"]
        except (json.JSONDecodeError, TypeError):
            data = e["data"]

        source = e["source"]
        etype = e["event_type"]

        if source == "ha" and isinstance(data, dict):
            entity = data.get("entity_id", "")
            state = data.get("state", "")
            friendly = data.get("friendly_name", entity)
            lines.append(f"- {friendly} ({entity}): {state}")
        else:
            lines.append(f"- [{source}/{etype}]: {json.dumps(data) if isinstance(data, dict) else data}")

    event_block = "\n".join(lines)
    return (
        f"[SYSTEM EVENT — act via jarvis-send/jarvis-photo only, do NOT reply in chat]\n"
        f"The following events just occurred:\n{event_block}\n\n"
        f"Check your DEFCON level in the knowledge graph to determine response verbosity.\n"
        f"If important: use jarvis-send to notify and jarvis-photo for snapshots.\n"
        f"If routine: do nothing. Do NOT produce any chat response."
    )


async def event_processor_loop(send_fn):
    """Poll events table, batch, route to container. Output is IPC-only."""
    log.info("Event processor started (poll every %ds, batch window %ds)", POLL_INTERVAL, BATCH_WINDOW)
    while True:
        try:
            events = get_unprocessed_events()
            if not events:
                await asyncio.sleep(POLL_INTERVAL)
                continue

            for e in events:
                mark_event_processed(e["id"])

            await asyncio.sleep(BATCH_WINDOW)
            more = get_unprocessed_events()
            for e in more:
                mark_event_processed(e["id"])
                events.append(e)

            # Check if any event is an alarm arm/disarm → trigger briefing
            alarm_state = None
            alarm_ts = None
            for e in events:
                state = _is_alarm_event(e)
                if state:
                    alarm_state = state
                    alarm_ts = e.get("timestamp")

            if alarm_state:
                prompt = _build_briefing(alarm_state, alarm_ts)
                log.info("Alarm %s detected — sending %s briefing to JARVIS",
                         alarm_state, "MORNING" if alarm_state == "disarmed" else "EVENING")
            else:
                prompt = _summarise_events(events)
                log.info("Routing %d event(s) to JARVIS container (IPC-only)", len(events))

            try:
                result = await run_in_container(prompt, DEFAULT_CHAT_ID)
                if result:
                    log.debug("Event container response (discarded): %s", result[:200])
            except Exception as e:
                log.error("Container failed on event batch: %s", e)

        except Exception as e:
            log.error("Event processor error: %s", e)
            await asyncio.sleep(POLL_INTERVAL)
