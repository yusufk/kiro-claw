"""Tests for webhook server."""

import json
import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from src.webhook import create_webhook_app
import src.webhook as webhook


@pytest.fixture
def app():
    """Create test app with no auth."""
    old_secret = webhook.WEBHOOK_SECRET
    old_ips = webhook.ALLOWED_IPS
    webhook.WEBHOOK_SECRET = ""
    webhook.ALLOWED_IPS = set()
    yield create_webhook_app()
    webhook.WEBHOOK_SECRET = old_secret
    webhook.ALLOWED_IPS = old_ips


@pytest.fixture
def secured_app():
    """Create test app with auth."""
    old_secret = webhook.WEBHOOK_SECRET
    old_ips = webhook.ALLOWED_IPS
    webhook.WEBHOOK_SECRET = "test-secret"
    webhook.ALLOWED_IPS = {"127.0.0.1"}
    yield create_webhook_app()
    webhook.WEBHOOK_SECRET = old_secret
    webhook.ALLOWED_IPS = old_ips


@pytest.mark.asyncio
async def test_health(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.get("/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_post_event(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.post("/event", json={
        "source": "test", "event_type": "ping", "data": {"msg": "hi"}
    })
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True
    assert "event_id" in data


@pytest.mark.asyncio
async def test_post_invalid_json(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.post("/event", data="not json", headers={"Content-Type": "application/json"})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_auth_rejected_bad_token(aiohttp_client, secured_app):
    client = await aiohttp_client(secured_app)
    resp = await client.post("/event", json={"source": "test"},
                             headers={"Authorization": "Bearer wrong"})
    assert resp.status == 403
