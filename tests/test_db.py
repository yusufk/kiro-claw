"""Tests for the SQLite database layer."""

import os
import sqlite3
import tempfile
import pytest

# Patch DB_PATH before importing
_tmp = tempfile.mktemp(suffix=".db")
os.environ.setdefault("KIROCLAW_TEST_DB", _tmp)

import src.db as db
db.DB_PATH = type(db.DB_PATH)(_tmp)


@pytest.fixture(autouse=True)
def fresh_db():
    """Ensure a clean DB for each test."""
    if os.path.exists(_tmp):
        os.unlink(_tmp)
    yield
    if os.path.exists(_tmp):
        os.unlink(_tmp)


class TestTasks:
    def test_create_and_get(self):
        db.create_task({
            "id": "task-001", "chat_id": 123, "prompt": "test",
            "schedule_type": "once", "schedule_value": "2026-01-01 00:00:00",
            "next_run": "2026-01-01 00:00:00", "status": "active",
            "created_at": "2026-01-01 00:00:00",
        })
        tasks = db.get_tasks_for_chat(123)
        assert len(tasks) == 1
        assert tasks[0]["id"] == "task-001"

    def test_delete_task(self):
        db.create_task({
            "id": "task-del", "chat_id": 123, "prompt": "x",
            "schedule_type": "once", "schedule_value": "2026-01-01 00:00:00",
            "next_run": "2026-01-01 00:00:00", "status": "active",
            "created_at": "2026-01-01 00:00:00",
        })
        assert db.delete_task("task-del") is True
        assert db.delete_task("task-del") is False

    def test_get_due_tasks(self):
        db.create_task({
            "id": "task-due", "chat_id": 123, "prompt": "overdue",
            "schedule_type": "once", "schedule_value": "2020-01-01 00:00:00",
            "next_run": "2020-01-01 00:00:00", "status": "active",
            "created_at": "2020-01-01 00:00:00",
        })
        due = db.get_due_tasks()
        assert any(t["id"] == "task-due" for t in due)

    def test_update_after_run(self):
        db.create_task({
            "id": "task-run", "chat_id": 123, "prompt": "x",
            "schedule_type": "interval", "schedule_value": "60000",
            "next_run": "2020-01-01 00:00:00", "status": "active",
            "created_at": "2020-01-01 00:00:00",
        })
        db.update_after_run("task-run", "2030-01-01 00:00:00", "done")
        tasks = db.get_tasks_for_chat(123)
        assert tasks[0]["next_run"] == "2030-01-01 00:00:00"

    def test_complete_task(self):
        db.create_task({
            "id": "task-once", "chat_id": 123, "prompt": "x",
            "schedule_type": "once", "schedule_value": "2020-01-01 00:00:00",
            "next_run": "2020-01-01 00:00:00", "status": "active",
            "created_at": "2020-01-01 00:00:00",
        })
        db.update_after_run("task-once", None, "done")
        tasks = db.get_tasks_for_chat(123)
        assert len(tasks) == 0  # completed tasks not returned


class TestMessages:
    def test_store_and_retrieve(self):
        db.store_message(123, "Yusuf", 72911340, "hello", "2026-01-01 00:00:00")
        db.store_message(123, "JARVIS", 0, "hi sir", "2026-01-01 00:00:01", is_bot=True)
        msgs = db.get_recent_messages(123)
        assert len(msgs) == 2
        assert msgs[0]["sender"] == "Yusuf"
        assert msgs[1]["is_bot"] == 1

    def test_recent_messages_limit(self):
        for i in range(10):
            db.store_message(123, "u", 1, f"msg{i}", f"2026-01-01 00:00:{i:02d}")
        msgs = db.get_recent_messages(123, limit=5)
        assert len(msgs) == 5


class TestEvents:
    def test_store_and_get_unprocessed(self):
        eid = db.store_event("ha", "motion", '{"entity":"sensor.x"}', "2026-01-01 00:00:00")
        assert eid > 0
        events = db.get_unprocessed_events()
        assert len(events) == 1
        assert events[0]["source"] == "ha"

    def test_mark_processed(self):
        eid = db.store_event("ha", "test", "{}", "2026-01-01 00:00:00")
        db.mark_event_processed(eid)
        events = db.get_unprocessed_events()
        assert len(events) == 0

    def test_get_recent_events(self):
        db.store_event("ha", "a", "{}", "2026-01-01 00:00:00")
        db.store_event("mqtt", "b", "{}", "2026-01-01 00:00:01")
        all_events = db.get_recent_events()
        assert len(all_events) == 2
        ha_events = db.get_recent_events(source="ha")
        assert len(ha_events) == 1


class TestTaskRuns:
    def test_log_run(self):
        db.log_run("task-x", "2026-01-01 00:00:00", 1500, "success", result="ok")
        # No assertion needed — just verify it doesn't crash
        # Could query directly if needed
        conn = db._conn()
        rows = conn.execute("SELECT * FROM task_runs WHERE task_id='task-x'").fetchall()
        assert len(rows) == 1
        assert rows[0]["duration_ms"] == 1500
