"""Event processor — DEFCON-based filtering + briefings.

DEFCON controls which events reach the JARVIS container:
  5 (lowest): Only alarm arm/disarm → briefings
  4: + doorbell, alarm triggered/tamper
  3: + night movement (7PM-7AM) with snapshots
  2: + all movement events
  1: + everything, all cameras, proactive checks

Auto-escalation: DEFCON bumps to 3 at 9PM, back to 5 at 7AM.
JARVIS can also bump DEFCON via knowledge graph.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .db import get_unprocessed_events, mark_event_processed, get_events_since
from .runner import run_in_container

log = logging.getLogger(__name__)

POLL_INTERVAL = 5
DEFAULT_CHAT_ID = 72911340
BATCH_WINDOW = 10
MEMORY_PATH = Path(__file__).parent.parent / "data" / "brain" / "memory.json"
# Fallback if brain isn't mounted at data/brain
MEMORY_ALT = Path("/Users/yusuf/Documents/Obsidian/Yusufs Vault/AI brain/memory.json")

_last_briefing_ts: datetime | None = None
_last_briefing_counts: str | None = None


def _read_defcon() -> int:
    """Read DEFCON level from knowledge graph. Default 5."""
    for p in (MEMORY_PATH, MEMORY_ALT):
        if not p.exists():
            continue
        try:
            for line in p.read_text().splitlines():
                obj = json.loads(line)
                if obj.get("name") == "JARVIS DEFCON":
                    for obs in obj.get("observations", []):
                        if obs.startswith("Current level:"):
                            return int(obs.split(":")[1].strip()[0])
        except Exception:
            pass
    return 5


def _auto_defcon() -> int:
    """Apply time-based auto-escalation on top of stored level."""
    stored = _read_defcon()
    hour = datetime.now().hour  # local time
    if 21 <= hour or hour < 7:  # 9PM - 7AM
        return min(stored, 3)  # at least DEFCON 3 at night
    return stored


def _classify_event(event: dict) -> str:
    """Classify event into: alarm_state, alarm_critical, doorbell, movement, other."""
    try:
        data = json.loads(event["data"]) if isinstance(event["data"], str) else event["data"]
    except (json.JSONDecodeError, TypeError):
        return "other"
    friendly = data.get("friendly_name", "").lower()
    state = data.get("state", "").lower()

    if "alarm" in friendly and ("armed" in friendly or "disarmed" in friendly):
        return "alarm_state"
    if "alarm" in friendly and ("triggered" in state or "tamper" in state):
        return "alarm_critical"
    if "doorbell" in friendly or "ring" in friendly:
        return "doorbell"
    if "movement" in friendly or "motion" in friendly:
        return "movement"
    return "other"


def _defcon_allows(event_class: str, defcon: int) -> bool:
    """Check if current DEFCON level allows this event class through."""
    if event_class == "alarm_state":
        return True  # always — triggers briefings
    if event_class == "alarm_critical":
        return defcon <= 4
    if event_class == "doorbell":
        return defcon <= 4
    if event_class == "movement":
        return defcon <= 3
    if event_class == "other":
        return defcon <= 2
    return defcon <= 1


def _is_alarm_event(event: dict) -> str | None:
    """Return 'armed' or 'disarmed' if this is an alarm state change."""
    try:
        data = json.loads(event["data"]) if isinstance(event["data"], str) else event["data"]
    except (json.JSONDecodeError, TypeError):
        return None
    friendly = data.get("friendly_name", "")
    if "Alarm - armed" in friendly:
        return "armed"
    if "Alarm - disarmed" in friendly:
        return "disarmed"
    return None


def _should_send_briefing(alarm_ts: str, counts_key: str) -> bool:
    """Dedup: skip if identical briefing sent within 5 min."""
    global _last_briefing_ts, _last_briefing_counts
    now = datetime.now(timezone.utc)
    if _last_briefing_ts and _last_briefing_counts == counts_key:
        if (now - _last_briefing_ts) < timedelta(minutes=5):
            return False
    _last_briefing_ts = now
    _last_briefing_counts = counts_key
    return True


def _build_briefing(alarm_state: str, event_ts: str, defcon: int) -> str | None:
    """Build a briefing prompt. Returns None if deduped."""
    try:
        ts = datetime.fromisoformat(event_ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        ts = datetime.now(timezone.utc)
    since = (ts - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
    recent = get_events_since(since)

    counts = {}
    for e in recent:
        try:
            d = json.loads(e["data"]) if isinstance(e["data"], str) else e["data"]
        except (json.JSONDecodeError, TypeError):
            d = {}
        name = d.get("friendly_name", e.get("source", "unknown"))
        counts[name] = counts.get(name, 0) + 1

    counts_key = json.dumps(counts, sort_keys=True)
    if not _should_send_briefing(event_ts, counts_key):
        log.info("Briefing suppressed (duplicate within 5 min)")
        return None

    summary_lines = [f"  - {name}: {n}x" for name, n in sorted(counts.items(), key=lambda x: -x[1])]
    summary = "\n".join(summary_lines) if summary_lines else "  No events recorded."

    if alarm_state == "disarmed":
        period, context = "MORNING", "Alarm disarmed (morning). Overnight summary."
    else:
        period, context = "EVENING", "Alarm armed (night). Daytime summary."

    return (
        f"[SYSTEM EVENT — BRIEFING REQUEST]\n"
        f"{context}\n\n"
        f"Events (last 12h):\n{summary}\n\n"
        f"Current DEFCON: {defcon}\n"
        f"Send a concise {period} BRIEFING via jarvis-send (3-5 lines).\n"
        f"If evening: also bump DEFCON to 3 in the knowledge graph for overnight monitoring."
    )


def _summarise_events(events: list[dict], defcon: int) -> str:
    """Build a prompt from a batch of events."""
    lines = []
    for e in events:
        try:
            data = json.loads(e["data"]) if isinstance(e["data"], str) else e["data"]
        except (json.JSONDecodeError, TypeError):
            data = e["data"]
        if isinstance(data, dict):
            friendly = data.get("friendly_name", data.get("entity_id", ""))
            state = data.get("state", "")
            lines.append(f"- {friendly}: {state}")
        else:
            lines.append(f"- {e['source']}: {data}")

    event_block = "\n".join(lines)
    return (
        f"[SYSTEM EVENT — act via jarvis-send/jarvis-photo only, do NOT reply in chat]\n"
        f"Current DEFCON: {defcon}\n"
        f"Events:\n{event_block}\n\n"
        f"Assess and act per DEFCON level. Use jarvis-send/jarvis-photo if warranted."
    )


async def event_processor_loop(send_fn):
    """Poll events, filter by DEFCON, route to container."""
    log.info("Event processor started (poll every %ds, batch window %ds)", POLL_INTERVAL, BATCH_WINDOW)
    while True:
        try:
            events = get_unprocessed_events()
            if not events:
                await asyncio.sleep(POLL_INTERVAL)
                continue

            for e in events:
                mark_event_processed(e["id"])

            await asyncio.sleep(BATCH_WINDOW)
            more = get_unprocessed_events()
            for e in more:
                mark_event_processed(e["id"])
                events.append(e)

            defcon = _auto_defcon()

            # Filter events by DEFCON level
            allowed = []
            alarm_state = None
            alarm_ts = None
            for e in events:
                cls = _classify_event(e)
                state = _is_alarm_event(e)
                if state:
                    alarm_state = state
                    alarm_ts = e.get("timestamp")
                if _defcon_allows(cls, defcon):
                    allowed.append(e)
                else:
                    log.debug("DEFCON %d filtered out: %s", defcon, cls)

            # Alarm arm/disarm → briefing (always, regardless of filter)
            if alarm_state:
                prompt = _build_briefing(alarm_state, alarm_ts, defcon)
                if prompt:
                    log.info("Alarm %s — sending %s briefing (DEFCON %d)",
                             alarm_state, "MORNING" if alarm_state == "disarmed" else "EVENING", defcon)
                    try:
                        await run_in_container(prompt, DEFAULT_CHAT_ID)
                    except Exception as e:
                        log.error("Briefing container failed: %s", e)

                # Also send remaining non-alarm events if any passed the filter
                allowed = [e for e in allowed if not _is_alarm_event(e)]

            if allowed:
                prompt = _summarise_events(allowed, defcon)
                log.info("Routing %d event(s) to JARVIS (DEFCON %d)", len(allowed), defcon)
                try:
                    await run_in_container(prompt, DEFAULT_CHAT_ID)
                except Exception as e:
                    log.error("Container failed on event batch: %s", e)
            elif not alarm_state:
                log.debug("All %d event(s) filtered at DEFCON %d", len(events), defcon)

        except Exception as e:
            log.error("Event processor error: %s", e)
            await asyncio.sleep(POLL_INTERVAL)
