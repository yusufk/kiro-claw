"""Event processor — polls events table and routes to JARVIS container.

Output is IPC-only: JARVIS uses jarvis-send/jarvis-photo to communicate.
The container's stdout response is logged but NEVER forwarded to Telegram.
This cleanly separates the event pipeline from the chat pipeline.
"""

import asyncio
import json
import logging

from .db import get_unprocessed_events, mark_event_processed
from .runner import run_in_container

log = logging.getLogger(__name__)

POLL_INTERVAL = 5
DEFAULT_CHAT_ID = 72911340
BATCH_WINDOW = 10


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

            prompt = _summarise_events(events)
            log.info("Routing %d event(s) to JARVIS container (IPC-only)", len(events))

            try:
                result = await run_in_container(prompt, DEFAULT_CHAT_ID)
                # Log but do NOT send to Telegram — IPC handles all output
                if result:
                    log.debug("Event container response (discarded): %s", result[:200])
            except Exception as e:
                log.error("Container failed on event batch: %s", e)

        except Exception as e:
            log.error("Event processor error: %s", e)
            await asyncio.sleep(POLL_INTERVAL)
