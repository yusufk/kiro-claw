"""Container runner — manages a persistent Docker container with kiro-cli."""

import asyncio
import json
import logging
import os
import re
import tempfile
from pathlib import Path

from .config import CONTAINER_IMAGE, CONTAINER_TIMEOUT, KIRO_AGENT, BRAIN_DIR, EXTRA_HOSTS, MCP_SECRETS

log = logging.getLogger(__name__)

OUTPUT_START = "---KIROCLAW_OUTPUT_START---"
OUTPUT_END = "---KIROCLAW_OUTPUT_END---"
CONTAINER_NAME = "kiroclaw-agent"

_PROJECT_DIR = Path(__file__).parent.parent
_AGENT_CONFIG = _PROJECT_DIR / "data" / "agent.json"
_BRAIN_DIR = Path(BRAIN_DIR) if BRAIN_DIR else None
KIRO_DATA = Path.home() / "Library" / "Application Support" / "kiro-cli"

_local_kiro_data = _PROJECT_DIR / "data" / "kiro-data"
if _local_kiro_data.exists():
    KIRO_DATA = _local_kiro_data

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

# Build redaction patterns from MCP secrets at import time
_REDACT_PATTERNS: list[re.Pattern] = []
for _val in MCP_SECRETS.values():
    if len(_val) >= 8:  # only redact non-trivial values
        _REDACT_PATTERNS.append(re.compile(re.escape(_val)))


def _clean(text: str) -> str:
    """Strip ANSI codes, kiro-cli prefix, and any leaked secrets."""
    text = _ANSI_RE.sub("", text)
    text = re.sub(r"^> ", "", text, flags=re.MULTILINE)
    for pat in _REDACT_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text.strip()


_proc = None  # Persistent container process
_lock = asyncio.Lock()
_first_message = True
_env_path = None  # Temp env file for secrets


def _cleanup_env():
    """Remove the temporary env file if it exists."""
    global _env_path
    if _env_path and os.path.exists(_env_path):
        os.unlink(_env_path)
        _env_path = None


async def _ensure_container():
    """Start the persistent container if not already running."""
    global _proc, _env_path
    if _proc and _proc.returncode is None:
        return

    log.info("Starting persistent container...")
    cmd = [
        "docker", "run", "--rm", "-i",
        "--name", CONTAINER_NAME,
        "-v", f"{_AGENT_CONFIG}:/home/node/.kiro/agents/{KIRO_AGENT}.json:rw",
        "-v", f"{KIRO_DATA}:/home/node/.local/share/kiro-cli:rw",
        CONTAINER_IMAGE,
    ]

    if _BRAIN_DIR and _BRAIN_DIR.exists():
        cmd.insert(-1, "-v")
        cmd.insert(-1, f"{_BRAIN_DIR}:/workspace/brain:rw")

    for entry in EXTRA_HOSTS.split(","):
        entry = entry.strip()
        if entry:
            cmd.insert(-1, "--add-host")
            cmd.insert(-1, entry)

    # Write secrets to temp file instead of -e flags (hides from ps aux)
    env_fd, _env_path = tempfile.mkstemp(prefix="kiroclaw-", suffix=".env")
    with os.fdopen(env_fd, "w") as f:
        for key, val in MCP_SECRETS.items():
            f.write(f"{key}={val}\n")
    os.chmod(_env_path, 0o600)
    cmd.insert(-1, "--env-file")
    cmd.insert(-1, _env_path)

    _proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Wait for READY signal, then clean up env file
    while True:
        line = await asyncio.wait_for(_proc.stdout.readline(), timeout=30)
        if b"KIROCLAW_READY" in line:
            log.info("Container ready")
            _cleanup_env()
            return


async def stream_from_container(prompt: str, chat_id: int):
    """Async generator — yields cleaned lines as they stream from the container."""
    global _first_message

    async with _lock:
        try:
            await _ensure_container()
        except Exception as e:
            log.error("Failed to start container: %s", e)
            yield f"Container startup failed: {e}"
            return

        payload = json.dumps({
            "prompt": prompt,
            "agent": KIRO_AGENT,
            "resume": not _first_message,
        })
        _first_message = False

        try:
            _proc.stdin.write((payload + "\n").encode())
            await _proc.stdin.drain()

            async for line in _read_stream():
                yield line
        except asyncio.TimeoutError:
            log.warning("Response timed out, killing container")
            await _kill_container()
            yield "I'm terribly sorry, Sir — I ran out of time processing that request."
        except Exception as e:
            log.error("Container error: %s", e)
            await _kill_container()
            yield f"Container error: {e}"


async def run_in_container(prompt: str, chat_id: int) -> str:
    """Non-streaming wrapper — collects all lines into one response."""
    lines = []
    async for line in stream_from_container(prompt, chat_id):
        lines.append(line)
    return "\n".join(lines) or "No response from container."


async def _read_stream():
    """Yield cleaned lines as they arrive between output markers."""
    capturing = False
    deadline = asyncio.get_event_loop().time() + CONTAINER_TIMEOUT
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError()
        line = await asyncio.wait_for(_proc.stdout.readline(), timeout=remaining)
        if not line:
            break
        text = line.decode(errors="replace").rstrip()
        if OUTPUT_START in text:
            capturing = True
            continue
        if OUTPUT_END in text:
            return
        if capturing and text.startswith("STREAM:"):
            cleaned = _clean(text[7:])
            if cleaned:
                yield cleaned


async def _kill_container():
    global _proc, _first_message
    _first_message = True
    _cleanup_env()
    try:
        p = await asyncio.create_subprocess_exec(
            "docker", "kill", CONTAINER_NAME,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await p.wait()
    except Exception:
        pass
    _proc = None
