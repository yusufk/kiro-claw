"""Tests for event processor — verifies IPC-only output pipeline."""

import json
import pytest
from unittest.mock import AsyncMock, patch

from src.events import _summarise_events


class TestSummariseEvents:
    def test_ha_event(self):
        events = [{"source": "ha", "event_type": "state_changed",
                    "data": json.dumps({"entity_id": "binary_sensor.driveway", "state": "on", "friendly_name": "Driveway Motion"})}]
        prompt = _summarise_events(events)
        assert "Driveway Motion" in prompt
        assert "jarvis-send" in prompt
        assert "do NOT reply in chat" in prompt

    def test_multiple_events_batched(self):
        events = [
            {"source": "ha", "event_type": "state_changed",
             "data": json.dumps({"entity_id": "sensor.a", "state": "on", "friendly_name": "Back Motion"})},
            {"source": "ha", "event_type": "state_changed",
             "data": json.dumps({"entity_id": "sensor.b", "state": "on", "friendly_name": "Front Motion"})},
        ]
        prompt = _summarise_events(events)
        assert "Back Motion" in prompt
        assert "Front Motion" in prompt

    def test_non_ha_event(self):
        events = [{"source": "mqtt", "event_type": "temperature",
                    "data": json.dumps({"value": 22.5})}]
        prompt = _summarise_events(events)
        assert "mqtt/temperature" in prompt

    def test_malformed_data(self):
        events = [{"source": "ha", "event_type": "test", "data": "not json {{{"}]
        prompt = _summarise_events(events)
        assert "not json" in prompt

    def test_prompt_instructs_ipc_only(self):
        """The prompt must tell JARVIS to use IPC, not chat responses."""
        events = [{"source": "ha", "event_type": "test",
                    "data": json.dumps({"entity_id": "x", "state": "on", "friendly_name": "Test"})}]
        prompt = _summarise_events(events)
        assert "jarvis-send" in prompt
        assert "jarvis-photo" in prompt
        assert "do NOT" in prompt
