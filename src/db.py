"""SQLite database — messages, events, tasks."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "kiro-claw.db"


def _conn() -> sqlite3.Connection:
    """Open a connection with the schema ensured. Caller must close it;
    prefer `with _db() as db:` which does that for you."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    _init_schema(db)
    return db


@contextmanager
def _db():
    """Connection that is always closed. Before 2026-11-15 every helper opened
    a connection and never closed it; the event poller calls these every 5s."""
    db = _conn()
    try:
        yield db
    finally:
        db.close()


def _init_schema(db: sqlite3.Connection):
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
    # Delivery tracking (added 2026-11): processed 0 = pending, 1 = done, -1 = failed
    cols = {r[1] for r in db.execute("PRAGMA table_info(events)")}
    if "attempts" not in cols:
        db.execute("ALTER TABLE events ADD COLUMN attempts INTEGER DEFAULT 0")
    if "last_error" not in cols:
        db.execute("ALTER TABLE events ADD COLUMN last_error TEXT")
    # Dedup (added 2026-11): optional upstream id, content fingerprint, suppressed-repeat count
    if "source_id" not in cols:
        db.execute("ALTER TABLE events ADD COLUMN source_id TEXT")
    if "fingerprint" not in cols:
        db.execute("ALTER TABLE events ADD COLUMN fingerprint TEXT")
    if "dup_count" not in cols:
        db.execute("ALTER TABLE events ADD COLUMN dup_count INTEGER DEFAULT 0")
    db.execute("CREATE INDEX IF NOT EXISTS idx_evt_srcid ON events(source, source_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_evt_fp ON events(fingerprint, timestamp)")
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


def create_task(task: dict) -> str:
    with _db() as db:
        db.execute(
            "INSERT INTO tasks (id, chat_id, prompt, schedule_type, schedule_value, next_run, status, created_at) "
            "VALUES (:id, :chat_id, :prompt, :schedule_type, :schedule_value, :next_run, :status, :created_at)",
            task,
        )
        db.commit()
        return task["id"]


def get_due_tasks() -> list[dict]:
    with _db() as db:
        rows = db.execute(
            "SELECT * FROM tasks WHERE status='active' AND next_run <= datetime('now')"
        ).fetchall()
        return [dict(r) for r in rows]


def get_tasks_for_chat(chat_id: int) -> list[dict]:
    with _db() as db:
        rows = db.execute("SELECT * FROM tasks WHERE chat_id=? AND status='active'", (chat_id,)).fetchall()
        return [dict(r) for r in rows]


def delete_task(task_id: str) -> bool:
    with _db() as db:
        cur = db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        db.commit()
        return cur.rowcount > 0


def cancel_task(task_id: str) -> bool:
    """Soft-cancel: mark a task inactive so the scheduler stops picking it up.

    Preferred over delete_task for recurring tasks — it survives races (the row
    stays for audit) and the scheduler re-checks status immediately before running.
    """
    with _db() as db:
        cur = db.execute(
            "UPDATE tasks SET status='cancelled' WHERE id=? AND status='active'", (task_id,)
        )
        db.commit()
        return cur.rowcount > 0


def get_task(task_id: str) -> dict | None:
    with _db() as db:
        row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None


def count_recent_runs(task_id: str, within_seconds: int) -> int:
    """How many times this task has run in the last `within_seconds`.

    Used by the scheduler's runaway-loop guard to auto-disable tasks that fire
    far too often (e.g. a mis-scheduled interval no-op guard).
    """
    with _db() as db:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=within_seconds)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        row = db.execute(
            "SELECT COUNT(*) AS n FROM task_runs WHERE task_id=? AND run_at >= ?",
            (task_id, cutoff),
        ).fetchone()
        return int(row["n"]) if row else 0


def update_after_run(task_id: str, next_run: str | None, result: str | None):
    with _db() as db:
        if next_run:
            db.execute("UPDATE tasks SET next_run=?, last_result=? WHERE id=?", (next_run, result, task_id))
        else:
            db.execute("UPDATE tasks SET status='completed', last_result=? WHERE id=?", (result, task_id))
        db.commit()


def log_run(task_id: str, run_at: str, duration_ms: int, status: str, result: str = None, error: str = None):
    with _db() as db:
        db.execute(
            "INSERT INTO task_runs (task_id, run_at, duration_ms, status, result, error) VALUES (?,?,?,?,?,?)",
            (task_id, run_at, duration_ms, status, result, error),
        )
        db.commit()


# --- Messages ---

def store_message(chat_id: int, sender: str, sender_id: int, content: str, timestamp: str, is_bot: bool = False):
    with _db() as db:
        db.execute(
            "INSERT INTO messages (chat_id, sender, sender_id, content, timestamp, is_bot) VALUES (?,?,?,?,?,?)",
            (chat_id, sender, sender_id, content, timestamp, 1 if is_bot else 0),
        )
        db.commit()


def get_recent_messages(chat_id: int, limit: int = 50) -> list[dict]:
    with _db() as db:
        rows = db.execute(
            "SELECT * FROM messages WHERE chat_id=? ORDER BY timestamp DESC LIMIT ?", (chat_id, limit)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


# --- Events ---

def store_event(source: str, event_type: str, data: str, timestamp: str,
                source_id: str | None = None, fingerprint: str | None = None) -> int:
    with _db() as db:
        cur = db.execute(
            "INSERT INTO events (source, event_type, data, timestamp, source_id, fingerprint) "
            "VALUES (?,?,?,?,?,?)",
            (source, event_type, data, timestamp, source_id, fingerprint),
        )
        db.commit()
        return cur.lastrowid


def find_duplicate_event(source: str, source_id: str | None, fingerprint: str | None,
                         id_since: str, fp_since: str) -> int | None:
    """Return the id of an earlier copy of this event, or None.

    An explicit (source, source_id) match is an exact replay. A fingerprint match
    only counts inside the shorter fp_since window (a burst of identical events).
    """
    with _db() as db:
        if source_id:
            row = db.execute(
                "SELECT id FROM events WHERE source=? AND source_id=? AND timestamp>=? "
                "ORDER BY id LIMIT 1", (source, source_id, id_since)).fetchone()
            if row:
                return row[0]
        if fingerprint:
            row = db.execute(
                "SELECT id FROM events WHERE fingerprint=? AND timestamp>=? "
                "ORDER BY id LIMIT 1", (fingerprint, fp_since)).fetchone()
            if row:
                return row[0]
    return None


def bump_event_dup(event_id: int):
    """Record that another identical event was suppressed."""
    with _db() as db:
        db.execute("UPDATE events SET dup_count=COALESCE(dup_count,0)+1 WHERE id=?", (event_id,))
        db.commit()


def get_unprocessed_events(limit: int = 20) -> list[dict]:
    with _db() as db:
        rows = db.execute(
            "SELECT * FROM events WHERE processed=0 ORDER BY timestamp LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


EVENT_PENDING, EVENT_DONE, EVENT_FAILED = 0, 1, -1


def mark_event_processed(event_id: int):
    with _db() as db:
        db.execute("UPDATE events SET processed=1 WHERE id=?", (event_id,))
        db.commit()


def mark_events_done(event_ids: list[int]):
    if not event_ids:
        return
    with _db() as db:
        db.executemany("UPDATE events SET processed=1 WHERE id=?", [(i,) for i in event_ids])
        db.commit()


def record_event_failure(event_ids: list[int], error: str, max_attempts: int) -> list[int]:
    """Count a failed delivery. Events that reach max_attempts are parked as
    failed (processed=-1) so they stop retrying; returns those ids."""
    if not event_ids:
        return []
    with _db() as db:
        marks = ",".join("?" * len(event_ids))
        db.execute(f"UPDATE events SET attempts=attempts+1, last_error=? WHERE id IN ({marks})",
                   (error[:500], *event_ids))
        rows = db.execute(f"SELECT id FROM events WHERE id IN ({marks}) AND attempts>=?",
                          (*event_ids, max_attempts)).fetchall()
        given_up = [r[0] for r in rows]
        if given_up:
            db.executemany("UPDATE events SET processed=-1 WHERE id=?", [(i,) for i in given_up])
        db.commit()
        return given_up


def get_recent_events(source: str = None, limit: int = 20) -> list[dict]:
    with _db() as db:
        if source:
            rows = db.execute(
                "SELECT * FROM events WHERE source=? ORDER BY timestamp DESC LIMIT ?", (source, limit)
            ).fetchall()
        else:
            rows = db.execute("SELECT * FROM events ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in reversed(rows)]


def get_events_since(since_ts: str) -> list[dict]:
    """Get all events since a given ISO timestamp."""
    with _db() as db:
        rows = db.execute(
            "SELECT * FROM events WHERE timestamp >= ? ORDER BY timestamp", (since_ts,)
        ).fetchall()
        return [dict(r) for r in rows]