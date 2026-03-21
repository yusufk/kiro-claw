"""SQLite task scheduler database."""

import sqlite3
import os
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "tasks.db"


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        chat_id INTEGER NOT NULL,
        prompt TEXT NOT NULL,
        schedule_type TEXT NOT NULL,  -- cron | interval | once
        schedule_value TEXT NOT NULL,
        next_run TEXT,
        status TEXT DEFAULT 'active',
        last_result TEXT,
        created_at TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS task_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL,
        run_at TEXT NOT NULL,
        duration_ms INTEGER,
        status TEXT,
        result TEXT,
        error TEXT
    )""")
    db.commit()
    return db


def create_task(task: dict) -> str:
    db = _conn()
    db.execute(
        "INSERT INTO tasks (id, chat_id, prompt, schedule_type, schedule_value, next_run, status, created_at) "
        "VALUES (:id, :chat_id, :prompt, :schedule_type, :schedule_value, :next_run, :status, :created_at)",
        task,
    )
    db.commit()
    return task["id"]


def get_due_tasks() -> list[dict]:
    db = _conn()
    rows = db.execute(
        "SELECT * FROM tasks WHERE status='active' AND next_run <= datetime('now')"
    ).fetchall()
    return [dict(r) for r in rows]


def get_tasks_for_chat(chat_id: int) -> list[dict]:
    db = _conn()
    rows = db.execute("SELECT * FROM tasks WHERE chat_id=? AND status='active'", (chat_id,)).fetchall()
    return [dict(r) for r in rows]


def delete_task(task_id: str) -> bool:
    db = _conn()
    cur = db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    db.commit()
    return cur.rowcount > 0


def update_after_run(task_id: str, next_run: str | None, result: str | None):
    db = _conn()
    if next_run:
        db.execute("UPDATE tasks SET next_run=?, last_result=? WHERE id=?", (next_run, result, task_id))
    else:
        db.execute("UPDATE tasks SET status='completed', last_result=? WHERE id=?", (result, task_id))
    db.commit()


def log_run(task_id: str, run_at: str, duration_ms: int, status: str, result: str = None, error: str = None):
    db = _conn()
    db.execute(
        "INSERT INTO task_runs (task_id, run_at, duration_ms, status, result, error) VALUES (?,?,?,?,?,?)",
        (task_id, run_at, duration_ms, status, result, error),
    )
    db.commit()
