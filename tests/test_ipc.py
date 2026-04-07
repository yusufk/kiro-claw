"""Tests for IPC file processing."""

import json
import tempfile
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock

import src.ipc as ipc


@pytest.fixture
def ipc_dir(tmp_path):
    """Use a temp dir for IPC."""
    old = ipc.IPC_DIR
    ipc.IPC_DIR = tmp_path
    yield tmp_path
    ipc.IPC_DIR = old


def _write_ipc(ipc_dir, data, name="test.json"):
    p = ipc_dir / name
    p.write_text(json.dumps(data))
    # Backdate mtime so it passes the 1s freshness check
    os.utime(p, (0, 0))
    return p


@pytest.mark.asyncio
async def test_process_message(ipc_dir):
    send_fn = AsyncMock()
    old = ipc.ALLOWED_CHAT_IDS
    ipc.ALLOWED_CHAT_IDS = set()  # allow all
    try:
        f = _write_ipc(ipc_dir, {"type": "message", "chat_id": 123, "text": "hello"})
        await ipc._process_file(f, send_fn)
        send_fn.assert_called_once_with(123, "hello")
        assert not f.exists()
    finally:
        ipc.ALLOWED_CHAT_IDS = old


@pytest.mark.asyncio
async def test_process_photo(ipc_dir, tmp_path):
    send_fn = AsyncMock()
    send_photo_fn = AsyncMock()
    photo = tmp_path / "test.jpg"
    photo.write_bytes(b"\xff\xd8\xff")
    old = ipc.ALLOWED_CHAT_IDS
    ipc.ALLOWED_CHAT_IDS = set()
    try:
        f = _write_ipc(ipc_dir, {
            "type": "photo", "chat_id": 123,
            "path": str(photo), "caption": "test shot"
        })
        await ipc._process_file(f, send_fn, send_photo_fn)
        send_photo_fn.assert_called_once_with(123, str(photo), "test shot")
        assert not f.exists()
    finally:
        ipc.ALLOWED_CHAT_IDS = old


@pytest.mark.asyncio
async def test_process_unknown_type(ipc_dir):
    send_fn = AsyncMock()
    f = _write_ipc(ipc_dir, {"type": "unknown_thing"})
    await ipc._process_file(f, send_fn)
    send_fn.assert_not_called()
    assert not f.exists()


@pytest.mark.asyncio
async def test_blocked_chat_id(ipc_dir):
    send_fn = AsyncMock()
    # Temporarily set allowed IDs to exclude 999
    old = ipc.ALLOWED_CHAT_IDS
    ipc.ALLOWED_CHAT_IDS = {123}
    try:
        f = _write_ipc(ipc_dir, {"type": "message", "chat_id": 999, "text": "blocked"})
        await ipc._process_file(f, send_fn)
        send_fn.assert_not_called()
    finally:
        ipc.ALLOWED_CHAT_IDS = old


@pytest.mark.asyncio
async def test_malformed_json(ipc_dir):
    send_fn = AsyncMock()
    f = ipc_dir / "bad.json"
    f.write_text("not json{{{")
    os.utime(f, (0, 0))
    await ipc._process_file(f, send_fn)
    send_fn.assert_not_called()
    # Should be moved to errors/
    assert (ipc_dir / "errors" / "bad.json").exists()
