"""Webhook server — receives events from HA and other sources."""

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from aiohttp import web

from .db import store_event, find_duplicate_event, bump_event_dup

log = logging.getLogger(__name__)

WEBHOOK_PORT = int(os.environ.get("WEBHOOK_PORT", "8099"))
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
# Identical events inside this window collapse into one (0 disables). A caller-supplied
# source_id is an exact replay key and is honoured for the longer ID window.
DEDUP_WINDOW_SECONDS = int(os.environ.get("EVENT_DEDUP_WINDOW_SECONDS", "30"))
SOURCE_ID_WINDOW_SECONDS = int(os.environ.get("EVENT_SOURCE_ID_WINDOW_SECONDS", "600"))
# Never collapse these by content: a second doorbell press or alarm trigger is news.
NEVER_FINGERPRINT = {"alarm_critical", "doorbell"}
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


def _fingerprint(source: str, event_type: str, payload) -> str:
    canon = json.dumps([source, event_type, payload], sort_keys=True, default=str)
    return hashlib.sha1(canon.encode()).hexdigest()


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
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%d %H:%M:%S")

    source_id = data.get("source_id")
    source_id = str(source_id)[:200] if source_id not in (None, "") else None
    fp = None
    if DEDUP_WINDOW_SECONDS > 0:
        from .events import _classify_event  # local import: events pulls in the runner
        if _classify_event({"data": json.dumps(payload)}) not in NEVER_FINGERPRINT:
            fp = _fingerprint(source, event_type, payload)

    if source_id or fp:
        fmt = "%Y-%m-%d %H:%M:%S"
        existing = find_duplicate_event(
            source, source_id, fp,
            (now - timedelta(seconds=SOURCE_ID_WINDOW_SECONDS)).strftime(fmt),
            (now - timedelta(seconds=max(DEDUP_WINDOW_SECONDS, 1))).strftime(fmt),
        )
        if existing:
            bump_event_dup(existing)
            log.info("Event duplicate of %d suppressed: %s/%s from %s", existing, source, event_type, request.remote)
            return web.json_response({"ok": True, "duplicate": True, "event_id": existing})

    event_id = store_event(source, event_type, json.dumps(payload), ts, source_id, fp)
    log.info("Event %d received: %s/%s from %s", event_id, source, event_type, request.remote)

    return web.json_response({"ok": True, "event_id": event_id})
