"""Container runner — manages a persistent Docker container with kiro-cli."""

import asyncio
import json
import logging
import re
from pathlib import Path

from .config import CONTAINER_IMAGE, CONTAINER_TIMEOUT, KIRO_AGENT, BRAIN_DIR, EXTRA_HOSTS

log = logging.getLogger(__name__)

OUTPUT_START = "---KIROCLAW_OUTPUT_START---"
OUTPUT_END = "---KIROCLAW_OUTPUT_END---"
CONTAINER_NAME = "kiroclaw-agent"

KIRO_CONFIG = Path.home() / ".kiro"
_BRAIN_DIR = Path(BRAIN_DIR) if BRAIN_DIR else None
KIRO_DATA = Path.home() / "Library" / "Application Support" / "kiro-cli"

_local_kiro_data = Path(__file__).parent.parent / "data" / "kiro-data"
if _local_kiro_data.exists():
    KIRO_DATA = _local_kiro_data

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _clean(text: str) -> str:
    """Strip ANSI codes, then remove kiro-cli's '> ' response prefix."""
    text = _ANSI_RE.sub("", text)
    text = re.sub(r"^> ", "", text, flags=re.MULTILINE)
    return text.strip()
_proc = None  # Persistent container process
_lock = asyncio.Lock()
_first_message = True


async def _ensure_container():
    """Start the persistent container if not already running."""
    global _proc
    if _proc and _proc.returncode is None:
        return

    log.info("Starting persistent container...")
    cmd = [
        "docker", "run", "--rm", "-i",
        "--name", CONTAINER_NAME,
        "-v", f"{KIRO_CONFIG}:/home/node/.kiro:rw",
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

    _proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Wait for READY signal
    while True:
        line = await asyncio.wait_for(_proc.stdout.readline(), timeout=30)
        if b"KIROCLAW_READY" in line:
            log.info("Container ready")
            return


async def run_in_container(prompt: str, chat_id: int) -> str:
    """Send a message to the persistent container and read the response."""
    global _first_message

    async with _lock:
        try:
            await _ensure_container()
        except Exception as e:
            log.error("Failed to start container: %s", e)
            return f"Container startup failed: {e}"

        payload = json.dumps({
            "prompt": prompt,
            "agent": KIRO_AGENT,
            "resume": not _first_message,
        })
        _first_message = False

        try:
            _proc.stdin.write((payload + "\n").encode())
            await _proc.stdin.drain()

            return await asyncio.wait_for(
                _read_response(), timeout=CONTAINER_TIMEOUT
            )
        except asyncio.TimeoutError:
            log.warning("Response timed out, killing container")
            await _kill_container()
            return "I'm terribly sorry, Sir — I ran out of time processing that request."
        except Exception as e:
            log.error("Container error: %s", e)
            await _kill_container()
            return f"Container error: {e}"


async def _read_response() -> str:
    """Read stdout until we get a complete output block."""
    buf = []
    capturing = False
    while True:
        line = await _proc.stdout.readline()
        if not line:
            break
        text = line.decode(errors="replace").rstrip()
        if OUTPUT_START in text:
            capturing = True
            continue
        if OUTPUT_END in text:
            raw = "\n".join(buf)
            try:
                data = json.loads(raw)
                result = data.get("result") or data.get("error") or raw
            except json.JSONDecodeError:
                result = raw
            return _clean(result)
        if capturing:
            buf.append(text)
    return _clean("\n".join(buf)) or "No response from container."


async def _kill_container():
    global _proc, _first_message
    _first_message = True
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
