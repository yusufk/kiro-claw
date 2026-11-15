"""Event dedup: explicit source_id replays and short-window identical bursts."""

import json
import pytest

import src.db as db
import src.webhook as webhook
from src.webhook import create_webhook_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(webhook, "WEBHOOK_SECRET", "")
    monkeypatch.setattr(webhook, "ALLOWED_IPS", set())
    monkeypatch.setattr(webhook, "EVENT_DEDUP_WINDOW_SECONDS", 30)
    return create_webhook_app()


def _motion(**extra):
    body = {"source": "ha", "event_type": "state_changed",
            "data": {"entity_id": "movement_back", "friendly_name": "Movement - back", "state": "on"}}
    body.update(extra)
    return body


def _rows():
    with db._db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM events ORDER BY id")]


@pytest.mark.asyncio
async def test_identical_burst_collapses_and_counts(aiohttp_client, app):
    client = await aiohttp_client(app)
    first = await (await client.post("/event", json=_motion())).json()
    second = await (await client.post("/event", json=_motion())).json()
    third = await (await client.post("/event", json=_motion())).json()
    assert not first.get("duplicate")
    assert second["duplicate"] and third["duplicate"]
    assert second["event_id"] == first["event_id"]
    rows = _rows()
    assert len(rows) == 1 and rows[0]["dup_count"] == 2


@pytest.mark.asyncio
async def test_different_state_is_not_a_duplicate(aiohttp_client, app):
    client = await aiohttp_client(app)
    await client.post("/event", json=_motion())
    other = _motion()
    other["data"]["state"] = "off"
    resp = await (await client.post("/event", json=other)).json()
    assert not resp.get("duplicate")
    assert len(_rows()) == 2


@pytest.mark.asyncio
async def test_source_id_replay_suppressed_even_if_payload_differs(aiohttp_client, app):
    client = await aiohttp_client(app)
    await client.post("/event", json=_motion(source_id="ctx-1"))
    changed = _motion(source_id="ctx-1")
    changed["data"]["state"] = "off"
    resp = await (await client.post("/event", json=changed)).json()
    assert resp["duplicate"] is True
    assert len(_rows()) == 1


@pytest.mark.asyncio
async def test_doorbell_and_alarm_triggers_never_collapse_by_content(aiohttp_client, app):
    client = await aiohttp_client(app)
    bell = {"source": "ha", "event_type": "state_changed",
            "data": {"entity_id": "doorbell", "friendly_name": "Doorbell", "state": "pressed"}}
    trig = {"source": "ha", "event_type": "state_changed",
            "data": {"entity_id": "alarm", "friendly_name": "Alarm panel", "state": "triggered"}}
    for body in (bell, bell, trig, trig):
        resp = await (await client.post("/event", json=body)).json()
        assert not resp.get("duplicate")
    assert len(_rows()) == 4


@pytest.mark.asyncio
async def test_window_zero_disables_fingerprint_dedup(aiohttp_client, app, monkeypatch):
    monkeypatch.setattr(webhook, "EVENT_DEDUP_WINDOW_SECONDS", 0)
    client = await aiohttp_client(app)
    await client.post("/event", json=_motion())
    resp = await (await client.post("/event", json=_motion())).json()
    assert not resp.get("duplicate")
    assert len(_rows()) == 2


@pytest.mark.asyncio
async def test_old_identical_event_outside_window_is_kept(aiohttp_client, app):
    client = await aiohttp_client(app)
    first = await (await client.post("/event", json=_motion())).json()
    with db._db() as c:  # age the first event past the window
        c.execute("UPDATE events SET timestamp='2020-01-01 00:00:00' WHERE id=?", (first["event_id"],))
        c.commit()
    resp = await (await client.post("/event", json=_motion())).json()
    assert not resp.get("duplicate")


def test_prompt_shows_burst_count():
    from src.events import _summarise_events
    ev = [{"source": "ha", "event_type": "x", "dup_count": 2,
           "data": json.dumps({"friendly_name": "Movement - back", "state": "on"})}]
    assert "x3 in a burst" in _summarise_events(ev, defcon=3)


@pytest.mark.asyncio
async def test_non_dict_payload_is_stored_not_500(aiohttp_client, app):
    client = await aiohttp_client(app)
    resp = await client.post("/event", json={"source": "ha", "event_type": "x", "data": "alarm fired now"})
    assert resp.status == 200
    assert len(_rows()) == 1


@pytest.mark.asyncio
async def test_failed_event_does_not_swallow_retry(aiohttp_client, app):
    client = await aiohttp_client(app)
    first = await (await client.post("/event", json=_motion(source_id="s1"))).json()
    with db._db() as c:
        c.execute("UPDATE events SET processed=-1 WHERE id=?", (first["event_id"],))
        c.commit()
    retry = await (await client.post("/event", json=_motion(source_id="s1"))).json()
    assert not retry.get("duplicate")
    assert len(_rows()) == 2


@pytest.mark.asyncio
async def test_triggered_state_never_collapses_even_with_armed_name(aiohttp_client, app):
    client = await aiohttp_client(app)
    body = {"source": "ha", "event_type": "state_changed",
            "data": {"entity_id": "alarm_control_panel.partition_outside",
                     "friendly_name": "Alarm - Armed", "state": "triggered"}}
    for _ in range(3):
        resp = await (await client.post("/event", json=body)).json()
        assert not resp.get("duplicate")
    assert len(_rows()) == 3


def test_briefing_counts_include_suppressed_repeats(monkeypatch):
    import src.events as ev
    rows = [{"source": "ha", "dup_count": 4,
             "data": json.dumps({"friendly_name": "Movement - back", "state": "on"})}]
    monkeypatch.setattr(ev, "get_events_since", lambda since: rows)
    monkeypatch.setattr(ev, "_should_send_briefing", lambda *a: True)
    prompt = ev._build_briefing("disarmed", "2026-01-01T07:00:00Z", 5)
    assert "Movement - back: 5x" in prompt


def test_classifier_survives_non_dict():
    from src.events import _classify_event, _is_alarm_event
    assert _classify_event({"data": json.dumps("just text")}) == "other"
    assert _classify_event({"data": json.dumps([1, 2])}) == "other"
    assert _is_alarm_event({"data": json.dumps(5)}) is None
