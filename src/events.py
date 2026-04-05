"""Event processor — polls events table and routes to JARVIS container for action."""

import asyncio
import json
import logging

from .db import get_unprocessed_events, mark_event_processed, get_recent_events
from .runner import run_in_container

log = logging.getLogger(__name__)

POLL_INTERVAL = 5  # seconds
DEFAULT_CHAT_ID = 72911340
BATCH_WINDOW = 10  # seconds — collect events before sending to JARVIS


def _summarise_events(events: list[dict]) -> str:
    """Build a prompt from a batch of events for JARVIS to reason about."""
    lines = []
    for e in events:
        try:
            data = json.loads(e["data"]) if isinstance(e["data"], str) else e["data"]
        except (json.JSONDecodeError, TypeError):
            data = e["data"]

        source = e["source"]
        etype = e["event_type"]

        if source == "ha":
            entity = data.get("entity_id", "")
            state = data.get("state", "")
            friendly = data.get("friendly_name", entity)
            lines.append(f"- {friendly} ({entity}): {state}")
        else:
            lines.append(f"- [{source}/{etype}]: {json.dumps(data) if isinstance(data, dict) else data}")

    event_block = "\n".join(lines)
    return (
        f"[SYSTEM EVENT — act on these if needed, notify me only if important]\n"
        f"The following events just occurred:\n{event_block}\n\n"
        f"Decide what to do. If it's routine (e.g. normal motion during daytime), "
        f"just acknowledge silently. If it needs my attention or action, tell me and/or take action."
    )


async def event_processor_loop(send_fn):
    """Poll events table, batch them, and route through JARVIS container."""
    log.info("Event processor started (poll every %ds, batch window %ds)", POLL_INTERVAL, BATCH_WINDOW)
    while True:
        try:
            events = get_unprocessed_events()
            if not events:
                await asyncio.sleep(POLL_INTERVAL)
                continue

            # Mark all as processed immediately to avoid re-processing
            for e in events:
                mark_event_processed(e["id"])

            # Wait briefly to batch rapid-fire events (e.g. multiple motion sensors)
            await asyncio.sleep(BATCH_WINDOW)
            more = get_unprocessed_events()
            for e in more:
                mark_event_processed(e["id"])
                events.append(e)

            prompt = _summarise_events(events)
            log.info("Routing %d event(s) to JARVIS container", len(events))

            try:
                result = await run_in_container(prompt, DEFAULT_CHAT_ID)
                if result and result.strip():
                    await send_fn(DEFAULT_CHAT_ID, result)
            except Exception as e:
                log.error("Container failed on event batch: %s", e)
                # Fallback: just notify with raw summary
                fallback = "\n".join(f"🏠 {l}" for l in prompt.split("\n") if l.startswith("- "))
                if fallback:
                    await send_fn(DEFAULT_CHAT_ID, fallback)

        except Exception as e:
            log.error("Event processor error: %s", e)
            await asyncio.sleep(POLL_INTERVAL)
