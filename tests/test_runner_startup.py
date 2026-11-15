"""Container startup must fail within a hard deadline, and failures must be
able to surface as exceptions for delivery-tracking callers.

Regression for 2026-11-15: a container that kept printing but never sent
KIROCLAW_READY held the runner lock for 4 hours and FRIDAY went silent.
"""

import asyncio
import os
import sqlite3
import tempfile

import pytest

import src.db as db
import src.runner as runner


class FakeStream:
    def __init__(self, lines, delay=0.0, forever=False):
        self._lines = list(lines)
        self._delay = delay
        self._forever = forever

    async def readline(self):
        await asyncio.sleep(self._delay)
        if self._lines:
            return self._lines.pop(0)
        if self._forever:
            return b"still booting...\n"
        return b""


class FakeProc:
    def __init__(self, stdout, stderr=None):
        self.stdout = stdout
        self.stderr = stderr or FakeStream([])
        self.stdin = None
        self.returncode = None

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


@pytest.fixture
def fake_docker(monkeypatch):
    """Patch subprocess creation: `docker run` gets the FakeProc, `docker kill` a no-op."""
    holder = {}

    async def fake_exec(*cmd, **kw):
        if cmd[:2] == ("docker", "kill"):
            p = FakeProc(FakeStream([]))
            p.returncode = 0
            return p
        return holder["proc"]

    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(runner, "_proc", None)
    return holder


def test_startup_times_out_when_ready_never_arrives(fake_docker, monkeypatch):
    monkeypatch.setattr(runner, "STARTUP_TIMEOUT", 0.3)
    fake_docker["proc"] = FakeProc(FakeStream([], delay=0.05, forever=True))

    async def go():
        with pytest.raises(runner.ContainerError, match="not ready"):
            await asyncio.wait_for(runner._ensure_container(), timeout=3)

    asyncio.run(go())
    assert runner._proc is None  # killed and cleared, lock not held hostage


def test_startup_fails_fast_on_container_exit(fake_docker, monkeypatch):
    monkeypatch.setattr(runner, "STARTUP_TIMEOUT", 30)
    fake_docker["proc"] = FakeProc(FakeStream([b"boom\n"]))  # then EOF

    async def go():
        with pytest.raises(runner.ContainerError, match="exited"):
            await asyncio.wait_for(runner._ensure_container(), timeout=3)

    asyncio.run(go())


def test_startup_succeeds_on_ready(fake_docker, monkeypatch):
    monkeypatch.setattr(runner, "STARTUP_TIMEOUT", 5)
    proc = FakeProc(FakeStream([b"loading\n", b"KIROCLAW_READY\n"]))
    fake_docker["proc"] = proc

    asyncio.run(runner._ensure_container())
    assert runner._proc is proc


def test_run_raises_when_asked_and_apologises_otherwise(fake_docker, monkeypatch):
    monkeypatch.setattr(runner, "STARTUP_TIMEOUT", 30)

    async def go():
        fake_docker["proc"] = FakeProc(FakeStream([]))  # immediate EOF
        with pytest.raises(runner.ContainerError):
            await runner.run_in_container("hi", 1, raise_errors=True)

        fake_docker["proc"] = FakeProc(FakeStream([]))
        out = await runner.run_in_container("hi", 1)
        assert "startup failed" in out

    asyncio.run(go())


def test_db_helpers_close_their_connections(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    monkeypatch.setattr(db, "DB_PATH", type(db.DB_PATH)(path))

    opened = []
    real_connect = sqlite3.connect

    def tracking_connect(*a, **kw):
        c = real_connect(*a, **kw)
        opened.append(c)
        return c

    monkeypatch.setattr(db.sqlite3, "connect", tracking_connect)
    for _ in range(5):
        db.get_unprocessed_events(limit=1)
        db.get_due_tasks()
    db.store_event("ha", "state_changed", "{}", "2026-11-15 00:00:00")

    assert len(opened) == 11
    for c in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            c.execute("SELECT 1")  # closed connections refuse queries
    os.unlink(path)
