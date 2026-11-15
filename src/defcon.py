"""DEFCON state, owned by the host.

The level lives in SQLite, not in the agent's knowledge graph, so the event filter
never depends on a model remembering to write it down. Who may change it:

  owner   Yusuf via /defcon: any level, raise or lower.
  agent   FRIDAY via jarvis-defcon: escalate only (a lower number). A request to
          relax is rejected, so a confused or manipulated agent cannot quieten the
          house. Only Yusuf or the alarm schedule can relax it.
  system  The host itself: evening arm sets at least 3, morning disarm resets to 5.
"""

import json
import logging
from pathlib import Path

from .config import BRAIN_DIR
from .db import get_state, set_state, add_defcon_history

log = logging.getLogger(__name__)

LEVELS = range(1, 6)
DEFAULT = 5
_KEY = "defcon"

# Legacy: older versions kept the level in the agent's knowledge graph (memory.json).
_LEGACY_PATHS = (
    Path(__file__).parent.parent / "data" / "brain" / "memory.json",
    (Path(BRAIN_DIR) / "memory.json") if BRAIN_DIR else None,
)


def _legacy_level() -> int | None:
    for p in _LEGACY_PATHS:
        if p is None or not p.exists():
            continue
        try:
            for line in p.read_text().splitlines():
                obj = json.loads(line)
                if obj.get("name") == "JARVIS DEFCON":
                    for obs in obj.get("observations", []):
                        if obs.startswith("Current level:"):
                            return int(obs.split(":")[1].strip()[0])
        except Exception:
            continue
    return None


def get_defcon() -> int:
    """Current stored level (1 most alert, 5 quietest). Seeds from the legacy graph once."""
    raw = get_state(_KEY)
    if raw is not None:
        try:
            level = int(raw)
            if level in LEVELS:
                return level
        except ValueError:
            pass
        log.warning("Invalid stored DEFCON %r, resetting to %d", raw, DEFAULT)
    seed = _legacy_level()
    level = seed if seed in LEVELS else DEFAULT
    set_state(_KEY, str(level), "seed")
    add_defcon_history(None, level, "system", "seeded from legacy graph" if seed else "default")
    return level


def request_defcon(level, actor: str, reason: str = "") -> tuple[bool, int, str]:
    """Try to change the level. Returns (applied, level_now, message)."""
    if isinstance(level, str):
        level = level.strip()
        level = int(level) if level.isascii() and level.isdigit() else None
    if isinstance(level, bool) or not isinstance(level, int):
        return False, get_defcon(), "level must be a whole number from 1 to 5"
    if level not in LEVELS:
        return False, get_defcon(), "level must be from 1 to 5"
    if actor not in ("owner", "agent", "system"):
        return False, get_defcon(), "unknown actor"

    current = get_defcon()
    if level == current:
        return True, current, f"already DEFCON {current}"
    if actor == "agent" and level > current:
        log.warning("Agent asked to relax DEFCON %d -> %d (rejected): %s", current, level, reason[:120])
        return False, current, "agents can only escalate; ask Yusuf to relax"

    set_state(_KEY, str(level), actor)
    add_defcon_history(current, level, actor, reason[:200])
    log.info("DEFCON %d -> %d by %s %s", current, level, actor, reason[:80])
    return True, level, f"DEFCON {current} -> {level}"
