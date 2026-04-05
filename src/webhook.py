"""Webhook server — receives events from HA and other sources."""

import json
import logging
from datetime import datetime, timezone

from aiohttp import web

from .db import store_event

log = logging.getLogger(__name__)

WEBHOOK_PORT = 8099
WEBHOOK_SECRET = None  # Set via env var if needed


def create_webhook_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/event", handle_event)
    app.router.add_get("/health", handle_health)
    return app


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def handle_event(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    source = data.get("source", "unknown")
    event_type = data.get("event_type", "generic")
    payload = data.get("data", data)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    event_id = store_event(source, event_type, json.dumps(payload), ts)
    log.info("Event %d received: %s/%s", event_id, source, event_type)

    return web.json_response({"ok": True, "event_id": event_id})
