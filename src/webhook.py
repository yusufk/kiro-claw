"""Webhook server — receives events from HA and other sources."""

import json
import logging
import os
from datetime import datetime, timezone

from aiohttp import web

from .db import store_event

log = logging.getLogger(__name__)

WEBHOOK_PORT = int(os.environ.get("WEBHOOK_PORT", "8099"))
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
ALLOWED_IPS = {ip.strip() for ip in os.environ.get("WEBHOOK_ALLOWED_IPS", "127.0.0.1,192.168.1.125").split(",") if ip.strip()}


def _check_auth(request: web.Request) -> str | None:
    """Return error message if request fails auth, None if OK."""
    # IP allowlist
    peer = request.remote
    if ALLOWED_IPS and peer not in ALLOWED_IPS:
        log.warning("Webhook rejected from IP %s", peer)
        return "forbidden"

    # Shared secret (if configured)
    if WEBHOOK_SECRET:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if token != WEBHOOK_SECRET:
            log.warning("Webhook rejected — bad secret from %s", peer)
            return "unauthorized"

    return None


def create_webhook_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/event", handle_event)
    app.router.add_get("/health", handle_health)
    return app


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def handle_event(request: web.Request) -> web.Response:
    err = _check_auth(request)
    if err:
        return web.json_response({"error": err}, status=403)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    source = data.get("source", "unknown")
    event_type = data.get("event_type", "generic")
    payload = data.get("data", data)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    event_id = store_event(source, event_type, json.dumps(payload), ts)
    log.info("Event %d received: %s/%s from %s", event_id, source, event_type, request.remote)

    return web.json_response({"ok": True, "event_id": event_id})
