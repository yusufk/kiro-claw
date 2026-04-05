"""Event processor — polls events table and acts on them."""

import asyncio
import json
import logging

from .db import get_unprocessed_events, mark_event_processed

log = logging.getLogger(__name__)

POLL_INTERVAL = 5  # seconds
DEFAULT_CHAT_ID = 72911340  # Owner's chat for notifications


def _format_event(event: dict) -> str:
    """Format an event for Telegram notification."""
    source = event["source"]
    etype = event["event_type"]
    try:
        data = json.loads(event["data"]) if isinstance(event["data"], str) else event["data"]
    except (json.JSONDecodeError, TypeError):
        data = event["data"]

    # HA events get a compact format
    if source == "ha":
        entity = data.get("entity_id", "")
        state = data.get("state", "")
        friendly = data.get("friendly_name", entity)
        if etype == "state_changed":
            return f"🏠 {friendly}: {state}"
        return f"🏠 [{etype}] {friendly}: {state}"

    # Generic events
    msg = data.get("message", json.dumps(data) if isinstance(data, dict) else str(data))
    return f"📡 [{source}/{etype}] {msg}"


async def event_processor_loop(send_fn):
    """Poll events table and forward to Telegram."""
    log.info("Event processor started (poll every %ds)", POLL_INTERVAL)
    while True:
        try:
            events = get_unprocessed_events()
            for event in events:
                text = _format_event(event)
                await send_fn(DEFAULT_CHAT_ID, text)
                mark_event_processed(event["id"])
                log.info("Event %d processed: %s", event["id"], text[:100])
        except Exception as e:
            log.error("Event processor error: %s", e)
        await asyncio.sleep(POLL_INTERVAL)
