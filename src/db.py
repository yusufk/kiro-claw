"""SQLite database — messages, events, tasks."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "kiro-claw.db"


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        sender TEXT,
        sender_id INTEGER,
        content TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        is_bot INTEGER DEFAULT 0
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(timestamp)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_msg_chat ON messages(chat_id)")
    db.execute("""CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        event_type TEXT,
        data TEXT,
        timestamp TEXT NOT NULL,
        processed INTEGER DEFAULT 0
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_evt_proc ON events(processed, timestamp)")
    db.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        chat_id INTEGER NOT NULL,
        prompt TEXT NOT NULL,
        schedule_type TEXT NOT NULL,
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


# --- Messages ---

def store_message(chat_id: int, sender: str, sender_id: int, content: str, timestamp: str, is_bot: bool = False):
    db = _conn()
    db.execute(
        "INSERT INTO messages (chat_id, sender, sender_id, content, timestamp, is_bot) VALUES (?,?,?,?,?,?)",
        (chat_id, sender, sender_id, content, timestamp, 1 if is_bot else 0),
    )
    db.commit()


def get_recent_messages(chat_id: int, limit: int = 50) -> list[dict]:
    db = _conn()
    rows = db.execute(
        "SELECT * FROM messages WHERE chat_id=? ORDER BY timestamp DESC LIMIT ?", (chat_id, limit)
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


# --- Events ---

def store_event(source: str, event_type: str, data: str, timestamp: str) -> int:
    db = _conn()
    cur = db.execute(
        "INSERT INTO events (source, event_type, data, timestamp) VALUES (?,?,?,?)",
        (source, event_type, data, timestamp),
    )
    db.commit()
    return cur.lastrowid


def get_unprocessed_events(limit: int = 20) -> list[dict]:
    db = _conn()
    rows = db.execute(
        "SELECT * FROM events WHERE processed=0 ORDER BY timestamp LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def mark_event_processed(event_id: int):
    db = _conn()
    db.execute("UPDATE events SET processed=1 WHERE id=?", (event_id,))
    db.commit()


def get_recent_events(source: str = None, limit: int = 20) -> list[dict]:
    db = _conn()
    if source:
        rows = db.execute(
            "SELECT * FROM events WHERE source=? ORDER BY timestamp DESC LIMIT ?", (source, limit)
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM events ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in reversed(rows)]
