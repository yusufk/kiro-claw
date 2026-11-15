"""Container runner — manages a persistent Docker container with kiro-cli."""

import asyncio
import collections
import json
import logging
import os
import re
import tempfile
from pathlib import Path

from .config import CONTAINER_IMAGE, CONTAINER_TIMEOUT, KIRO_AGENT, BRAIN_DIR, BRAIN_MOUNTS, EXTRA_HOSTS, MCP_SECRETS, PROJECTS, MEMPALACE_VENV, MEMPALACE_PALACE

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
_AUTH_URL_RE = re.compile(r"(https?://\S*(device|authorize|verify|sso|oidc)\S*)", re.IGNORECASE)

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


# Lines matching these patterns are tool execution noise — never show to user
_NOISE_RE = re.compile(
    r"(Running tool |Completed in \d|^ ⋮|^\s*\{|\^\s*\}|^\s*\"[a-z_]+\":|"
    r"from mcp server:|with the param)", re.IGNORECASE
)


_proc = None  # Persistent container process
_lock = asyncio.Lock()
_first_message = True
_env_path = None  # Temp env file for secrets
_stderr_task = None  # Drains container stderr so the pipe never fills
_stderr_tail: collections.deque = collections.deque(maxlen=20)

# Hard ceiling on container startup. Before 2026-11-15 the READY wait only had a
# per-line timeout, so a container that kept printing but never became ready
# held _lock forever and FRIDAY went silent for 4 hours.
STARTUP_TIMEOUT = int(os.getenv("STARTUP_TIMEOUT", "90"))


class ContainerError(RuntimeError):
    """The container failed to start, timed out, or died mid-response."""


async def _drain_stderr(stream):
    """Read stderr continuously; keep a short tail for error reports."""
    try:
        while True:
            line = await stream.readline()
            if not line:
                return
            text = _clean(line.decode(errors="replace"))[:500]
            if text:
                _stderr_tail.append(text)
                log.debug("[container stderr] %s", text)
    except Exception:
        pass


def _cleanup_env():
    """Remove the temporary env file if it exists."""
    global _env_path
    if _env_path and os.path.exists(_env_path):
        os.unlink(_env_path)
        _env_path = None


def _brain_volumes(brain_dir: Path | None, mounts: str) -> list[str]:
    """Docker -v specs for the brain.

    With no BRAIN_MOUNTS, mount the whole brain read-write (old behaviour).
    Otherwise mount only the listed paths, so the container cannot read notes
    it has no business with (2026-11-15: the whole-vault mount exposed family
    ID numbers to FRIDAY). Paths are relative to brain_dir; a ":ro" suffix makes
    one read-only. Entries that escape brain_dir or do not exist are skipped,
    except a missing name with no extension, which is created as a writable
    directory (for FRIDAY's own notes).
    """
    if not brain_dir or not brain_dir.exists():
        return []
    if not mounts.strip():
        return [f"{brain_dir}:/workspace/brain:rw"]

    root = brain_dir.resolve()
    specs = []
    for entry in mounts.split(","):
        entry = entry.strip()
        if not entry:
            continue
        mode = "rw"
        if entry.endswith(":ro"):
            entry, mode = entry[:-3].rstrip(), "ro"
        src = (brain_dir / entry).resolve()
        if src != root and root not in src.parents:
            log.warning("BRAIN_MOUNTS entry escapes brain dir, skipped: %s", entry)
            continue
        if not src.exists():
            if Path(entry).suffix:
                log.warning("BRAIN_MOUNTS entry not found, skipped: %s", entry)
                continue
            src.mkdir(parents=True, exist_ok=True)
        rel = src.relative_to(root).as_posix()
        specs.append(f"{src}:/workspace/brain/{rel}:{mode}")
    return specs


async def _ensure_container():
    """Start the persistent container if not already running."""
    global _proc, _env_path, _stderr_task
    if _proc and _proc.returncode is None:
        return

    log.info("Starting persistent container...")
    cmd = [
        "docker", "run", "--rm", "-i",
        "--name", CONTAINER_NAME,
        # FRIDAY lives in Johannesburg — give the container SAST so its clock,
        # `date`, and any time-of-day reasoning match the user's local time.
        # (Fixes the 2026-10-24 night-watch bug where FRIDAY compared against UTC.)
        "-e", "TZ=Africa/Johannesburg",
        "-v", f"{_AGENT_CONFIG}:/home/node/.kiro/agents/{KIRO_AGENT}.json:rw",
        "-v", f"{KIRO_DATA}:/home/node/.local/share/kiro-cli:rw",
        CONTAINER_IMAGE,
    ]

    for spec in _brain_volumes(_BRAIN_DIR, BRAIN_MOUNTS):
        cmd.insert(-1, "-v")
        cmd.insert(-1, spec)

    # Mount IPC directory for proactive messaging and task scheduling
    _ipc_dir = _PROJECT_DIR / "data" / "ipc"
    _ipc_dir.mkdir(parents=True, exist_ok=True)
    cmd.insert(-1, "-v")
    cmd.insert(-1, f"{_ipc_dir}:/workspace/ipc:rw")

    # Mount scratch directory for photos/snapshots (shared between container and host)
    _scratch_dir = _PROJECT_DIR / "data" / "scratch"
    _scratch_dir.mkdir(parents=True, exist_ok=True)
    cmd.insert(-1, "-v")
    cmd.insert(-1, f"{_scratch_dir}:/workspace/scratch:rw")

    # Mount mempalace venv + palace for FRIDAY's memory (per-machine, from env).
    # Paths come from MEMPALACE_VENV / MEMPALACE_PALACE (set in .env); each mount
    # is skipped when the var is unset or the path is absent, so this is a no-op
    # on machines without a local mempalace (e.g. the macbook node).
    _mempalace_venv_env = MEMPALACE_VENV.strip()
    _mempalace_palace_env = MEMPALACE_PALACE.strip()
    if _mempalace_venv_env and Path(_mempalace_venv_env).exists():
        cmd.insert(-1, "-v")
        cmd.insert(-1, f"{_mempalace_venv_env}:/opt/mempalace-venv:ro")
        log.info("Mounting mempalace venv from %s", _mempalace_venv_env)
    if _mempalace_palace_env and Path(_mempalace_palace_env).exists():
        cmd.insert(-1, "-v")
        cmd.insert(-1, f"{_mempalace_palace_env}:/workspace/mempalace:rw")
        log.info("Mounting mempalace palace from %s", _mempalace_palace_env)

    for entry in EXTRA_HOSTS.split(","):
        entry = entry.strip()
        if entry:
            cmd.insert(-1, "--add-host")
            cmd.insert(-1, entry)

    # Mount project directories (read-write)
    for proj in PROJECTS.split(","):
        proj = proj.strip()
        if proj and Path(proj).exists():
            name = Path(proj).name
            cmd.insert(-1, "-v")
            cmd.insert(-1, f"{proj}:/workspace/projects/{name}:rw")
            log.info("Mounting project: %s -> /workspace/projects/%s", proj, name)

    # Write secrets to temp file instead of -e flags (hides from ps aux)
    env_fd, _env_path = tempfile.mkstemp(prefix="kiroclaw-", suffix=".env")
    with os.fdopen(env_fd, "w") as f:
        for key, val in MCP_SECRETS.items():
            f.write(f"{key}={val}\n")
        # Pass KIRO_API_KEY for headless auth (no device flow needed)
        kiro_key = os.environ.get("KIRO_API_KEY", "")
        if kiro_key:
            f.write(f"KIRO_API_KEY={kiro_key}\n")
    os.chmod(_env_path, 0o600)
    cmd.insert(-1, "--env-file")
    cmd.insert(-1, _env_path)

    _stderr_tail.clear()
    _proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stderr_task = asyncio.create_task(_drain_stderr(_proc.stderr))

    # Wait for READY signal within an overall deadline, then clean up env file
    loop = asyncio.get_running_loop()
    deadline = loop.time() + STARTUP_TIMEOUT
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ContainerError(f"container not ready after {STARTUP_TIMEOUT}s")
            try:
                line = await asyncio.wait_for(_proc.stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                raise ContainerError(f"container not ready after {STARTUP_TIMEOUT}s") from None
            if not line:
                raise ContainerError("container exited before it was ready")
            if b"KIROCLAW_READY" in line:
                log.info("Container ready")
                _cleanup_env()
                return
    except ContainerError as e:
        tail = " | ".join(list(_stderr_tail)[-5:])
        log.error("Container startup failed: %s%s", e, f" (stderr: {tail})" if tail else "")
        await _kill_container()
        raise


async def stream_from_container(prompt: str, chat_id: int, raise_errors: bool = False):
    """Async generator — yields cleaned lines as they stream from the container.

    By default failures are yielded as an apology line (chat UX). With
    raise_errors=True they raise ContainerError instead, so callers that track
    delivery (the event processor) can retry rather than count it as done."""
    global _first_message

    async with _lock:
        try:
            await _ensure_container()
        except Exception as e:
            log.error("Failed to start container: %s", e)
            if raise_errors:
                raise ContainerError(f"startup failed: {e}") from e
            yield f"Container startup failed: {e}"
            return

        payload = json.dumps({
            "prompt": prompt,
            "agent": KIRO_AGENT,
            "resume": not _first_message,
            "chat_id": chat_id,
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
            if raise_errors:
                raise ContainerError("response timed out") from None
            yield "I'm terribly sorry, Sir — I ran out of time processing that request."
        except Exception as e:
            log.error("Container error: %s", e)
            await _kill_container()
            if raise_errors:
                raise ContainerError(str(e)) from e
            yield f"Container error: {e}"


async def run_in_container(prompt: str, chat_id: int, raise_errors: bool = False) -> str:
    """Non-streaming wrapper — collects all lines into one response."""
    lines = []
    async for line in stream_from_container(prompt, chat_id, raise_errors=raise_errors):
        lines.append(line)
    return "\n".join(lines) or "No response from container."


async def _read_stream():
    """Yield cleaned lines as they arrive between output markers."""
    capturing = False
    deadline = asyncio.get_running_loop().time() + CONTAINER_TIMEOUT
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
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
                # Detect auth URLs and yield them prominently
                auth_match = _AUTH_URL_RE.search(cleaned)
                if auth_match:
                    yield f"🔑 Auth required — open this link:\n\n{auth_match.group(1)}"
                elif not _NOISE_RE.search(cleaned):
                    yield cleaned


async def _kill_container():
    global _proc, _first_message, _stderr_task
    _first_message = True
    _cleanup_env()
    try:
        p = await asyncio.create_subprocess_exec(
            "docker", "kill", CONTAINER_NAME,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(p.wait(), timeout=30)
    except Exception:
        pass
    # Reap the `docker run` client so it can't linger holding pipes
    if _proc and _proc.returncode is None:
        try:
            _proc.kill()
            await asyncio.wait_for(_proc.wait(), timeout=10)
        except Exception:
            pass
    if _stderr_task and not _stderr_task.done():
        _stderr_task.cancel()
    _stderr_task = None
    _proc = None
