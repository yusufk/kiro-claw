"""The container must trust tools by name: on kiro-cli 3.3.0,
--no-interactive --trust-all-tools hides every MCP tool from the model."""

import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "entrypoint", Path(__file__).parent.parent / "container" / "entrypoint.py")
entrypoint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(entrypoint)


def _agent(tmp_path, monkeypatch, body):
    agents = tmp_path / ".kiro" / "agents"
    agents.mkdir(parents=True)
    (agents / "FRIDAY.json").write_text(body if isinstance(body, str) else json.dumps(body))
    monkeypatch.setenv("HOME", str(tmp_path))


def test_trusts_declared_tools_by_name(tmp_path, monkeypatch):
    _agent(tmp_path, monkeypatch, {"tools": ["@memory", "@home-assistant", "shell", "write"]})
    assert entrypoint._trust_args("FRIDAY") == ["--trust-tools=@memory,@home-assistant,shell,write"]


def test_falls_back_when_agent_missing_or_wildcard(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert entrypoint._trust_args("FRIDAY") == ["--trust-all-tools"]
    _agent(tmp_path, monkeypatch, {"tools": ["*"]})
    assert entrypoint._trust_args("FRIDAY") == ["--trust-all-tools"]


def test_falls_back_on_bad_json(tmp_path, monkeypatch):
    _agent(tmp_path, monkeypatch, "{not json")
    assert entrypoint._trust_args("FRIDAY") == ["--trust-all-tools"]
