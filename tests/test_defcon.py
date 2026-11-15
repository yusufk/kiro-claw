"""DEFCON is host-owned SQLite state; agents may escalate but never relax."""

import json
import pytest
from unittest.mock import AsyncMock

import src.db as db
import src.defcon as defcon
import src.events as events
import src.ipc as ipc


@pytest.fixture(autouse=True)
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(defcon, "_LEGACY_PATHS", (None,))
    return tmp_path


def test_default_is_5_and_seeds_state():
    assert defcon.get_defcon() == 5
    assert db.get_state("defcon") == "5"


def test_seeds_from_legacy_graph_once(tmp_path, monkeypatch):
    g = tmp_path / "memory.json"
    g.write_text(json.dumps({"name": "JARVIS DEFCON", "observations": ["Current level: 2"]}) + "\n")
    monkeypatch.setattr(defcon, "_LEGACY_PATHS", (g,))
    assert defcon.get_defcon() == 2
    g.write_text("")  # graph no longer consulted once seeded
    assert defcon.get_defcon() == 2


def test_agent_can_escalate_not_relax():
    defcon.request_defcon(3, "owner")
    ok, level, _ = defcon.request_defcon(2, "agent", "odd noise")
    assert ok and level == 2
    ok, level, msg = defcon.request_defcon(5, "agent", "all quiet")
    assert not ok and level == 2 and "escalate" in msg
    assert defcon.get_defcon() == 2


def test_owner_can_relax():
    defcon.request_defcon(1, "owner")
    ok, level, _ = defcon.request_defcon(5, "owner")
    assert ok and level == 5


@pytest.mark.parametrize("bad", [0, 6, "x", None, -1, 2.5e9])
def test_invalid_levels_rejected(bad):
    ok, level, _ = defcon.request_defcon(bad, "owner")
    assert not ok and level == 5


def test_history_records_actor_and_reason():
    defcon.request_defcon(2, "agent", "gate motion")
    h = db.get_defcon_history(1)[0]
    assert (h["old_level"], h["new_level"], h["actor"], h["reason"]) == (5, 2, "agent", "gate motion")


def test_alarm_schedule_drives_level():
    events._apply_alarm_defcon("armed")
    assert defcon.get_defcon() == 3
    defcon.request_defcon(1, "agent", "incident")
    events._apply_alarm_defcon("armed")       # evening must not relax an escalation
    assert defcon.get_defcon() == 1
    events._apply_alarm_defcon("disarmed")    # morning resets
    assert defcon.get_defcon() == 5


def test_corrupt_state_falls_back():
    db.set_state("defcon", "banana", "test")
    assert defcon.get_defcon() == 5


@pytest.mark.asyncio
async def test_ipc_set_defcon_only_escalates(tmp_path):
    f = tmp_path / "d.json"
    f.write_text(json.dumps({"type": "set_defcon", "level": 2, "reason": "r"}))
    await ipc._process_file(f, AsyncMock())
    assert defcon.get_defcon() == 2
    f.write_text(json.dumps({"type": "set_defcon", "level": 5}))
    await ipc._process_file(f, AsyncMock())
    assert defcon.get_defcon() == 2


def test_briefing_no_longer_asks_agent_to_edit_graph(monkeypatch):
    monkeypatch.setattr(events, "get_events_since", lambda since: [])
    monkeypatch.setattr(events, "_should_send_briefing", lambda *a: True)
    prompt = events._build_briefing("armed", "2026-01-01T20:00:00Z", 3)
    assert "knowledge graph" not in prompt and "jarvis-defcon" in prompt
