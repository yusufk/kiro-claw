"""Event delivery: events are marked done only after a successful run,
retried on failure, and parked as failed after MAX_ATTEMPTS."""

import asyncio
import json
import os
import tempfile

import pytest

_tmp = tempfile.mktemp(suffix=".db")

import src.db as db  # noqa: E402
import src.events as ev  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    # Per-test DB path, restored afterwards, so other test modules keep theirs
    monkeypatch.setattr(db, "DB_PATH", type(db.DB_PATH)(_tmp))
    if os.path.exists(_tmp):
        os.unlink(_tmp)
    monkeypatch.setattr(ev, "BATCH_WINDOW", 0)
    monkeypatch.setattr(ev, "POLL_INTERVAL", 0)
    monkeypatch.setattr(ev, "_auto_defcon", lambda: 1)          # let everything through
    yield
    if os.path.exists(_tmp):
        os.unlink(_tmp)


def add(entity="binary_sensor.back_movement", state="on"):
    return db.store_event("home_assistant", "state_changed",
                          json.dumps({"entity_id": entity, "state": state}), "2026-11-15T20:00:00Z")


def row(event_id):
    c = db._conn()
    return dict(c.execute("SELECT processed, attempts, last_error FROM events WHERE id=?", (event_id,)).fetchone())


def run_one_pass(monkeypatch, runner):
    """Run the processor loop until it has made one delivery attempt."""
    calls = []

    async def fake_run(prompt, chat_id):
        calls.append(prompt)
        result = runner(len(calls))
        if isinstance(result, Exception):
            raise result

    monkeypatch.setattr(ev, "run_in_container", fake_run)

    async def go():
        task = asyncio.create_task(ev.event_processor_loop(lambda *a: None))
        for _ in range(300):
            await asyncio.sleep(0.01)
            if not db.get_unprocessed_events():
                break
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(go())
    return calls


def test_event_marked_done_only_after_success(monkeypatch):
    eid = add()
    calls = run_one_pass(monkeypatch, lambda n: None)
    assert len(calls) == 1
    assert row(eid)["processed"] == db.EVENT_DONE


def test_failed_run_is_retried_then_parked(monkeypatch):
    eid = add()
    calls = run_one_pass(monkeypatch, lambda n: RuntimeError("container died"))
    r = row(eid)
    # every attempt re-delivered the same event, until MAX_ATTEMPTS
    assert len(calls) == ev.MAX_ATTEMPTS
    assert r["attempts"] == ev.MAX_ATTEMPTS and "container died" in r["last_error"]
    assert r["processed"] == db.EVENT_FAILED


def test_success_after_a_failure_marks_done(monkeypatch):
    eid = add()
    calls = run_one_pass(monkeypatch, lambda n: RuntimeError("blip") if n == 1 else None)
    assert len(calls) == 2
    assert row(eid)["processed"] == db.EVENT_DONE and row(eid)["attempts"] == 1


def test_gives_up_after_max_attempts():
    eid = add()
    for _ in range(ev.MAX_ATTEMPTS - 1):
        assert db.record_event_failure([eid], "boom", ev.MAX_ATTEMPTS) == []
    assert db.record_event_failure([eid], "boom", ev.MAX_ATTEMPTS) == [eid]
    assert row(eid)["processed"] == db.EVENT_FAILED
    assert db.get_unprocessed_events() == []          # no longer retried


def test_filtered_events_are_marked_done_without_a_run(monkeypatch):
    monkeypatch.setattr(ev, "_auto_defcon", lambda: 5)  # strictest: movement filtered
    eid = add()
    calls = []

    async def fake_run(prompt, chat_id):
        calls.append(prompt)

    monkeypatch.setattr(ev, "run_in_container", fake_run)

    async def go():
        task = asyncio.create_task(ev.event_processor_loop(lambda *a: None))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if row(eid)["processed"] == db.EVENT_DONE:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(go())
    assert calls == []
    assert row(eid)["processed"] == db.EVENT_DONE


def test_migration_adds_columns_to_existing_db():
    import sqlite3
    old = sqlite3.connect(_tmp)
    old.execute("CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, "
                "event_type TEXT, data TEXT, timestamp TEXT NOT NULL, processed INTEGER DEFAULT 0)")
    old.execute("INSERT INTO events (source, timestamp) VALUES ('x', '2026-01-01')")
    old.commit(); old.close()
    cols = {r[1] for r in db._conn().execute("PRAGMA table_info(events)")}
    assert {"attempts", "last_error"} <= cols
    assert db.get_unprocessed_events()[0]["attempts"] == 0
