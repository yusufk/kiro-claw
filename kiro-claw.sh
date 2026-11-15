#!/bin/bash
# Kiro-Claw launcher — runs the Telegram bot as a background process
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$DIR/data/kiro-claw.pid"
LOGFILE="$DIR/data/kiro-claw.log"

mkdir -p "$DIR/data"

# Resolve Python interpreter generically (no hardcoded paths):
#   1. KIRO_CLAW_PYTHON env var (explicit override)
#   2. a virtualenv sitting next to the repo ($DIR/.venv)
#   3. whatever python3 is on PATH
if [ -n "${KIRO_CLAW_PYTHON:-}" ]; then
  PYTHON="$KIRO_CLAW_PYTHON"
elif [ -x "$DIR/.venv/bin/python3" ]; then
  PYTHON="$DIR/.venv/bin/python3"
else
  PYTHON="$(command -v python3)"
fi

# Ensure agent.json exists (copy from template if missing)
if [ ! -f "$DIR/data/agent.json" ]; then
  cp "$DIR/agent.json.example" "$DIR/data/agent.json"
  echo "Created data/agent.json from template — edit secrets or ensure .env has them"
fi

CMD="${1:-start}"

case "$CMD" in
  start|stop|restart|status|logs) ;;
  *) echo "Usage: $0 {start|stop|restart|status|logs}"; exit 1 ;;
esac

# On a host where systemd owns the bot (cappucino), this script must not manage
# the process: `start` would launch a second copy and its port-8099 cleanup would
# SIGKILL the systemd-managed one, causing a crash loop. Point at systemd instead.
if command -v systemctl >/dev/null 2>&1 && systemctl cat kiro-claw >/dev/null 2>&1; then
  case "$CMD" in
    status) exec systemctl status kiro-claw --no-pager ;;
    logs)   exec journalctl -u kiro-claw -f ;;
    *)
      echo "Refusing to '$CMD': systemd owns kiro-claw on this host." >&2
      echo "Use: sudo systemctl $CMD kiro-claw" >&2
      exit 1
      ;;
  esac
fi

case "$CMD" in
  start)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "Already running (PID $(cat "$PIDFILE"))"
      exit 0
    fi
    # Kill any orphan holding the webhook port
    lsof -ti :8099 | xargs kill -9 2>/dev/null || true
    echo "Starting Kiro-Claw..."
    cd "$DIR"
    nohup "$PYTHON" -m src.main >> "$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    echo "Started (PID $!), logging to $LOGFILE"
    ;;
  stop)
    if [ -f "$PIDFILE" ]; then
      PID=$(cat "$PIDFILE")
      echo "Stopping Kiro-Claw (PID $PID)..."
      kill "$PID" 2>/dev/null || true
      docker kill kiroclaw-agent 2>/dev/null || true
      rm -f "$PIDFILE"
      echo "Stopped"
    else
      echo "Not running"
    fi
    ;;
  restart)
    "$0" stop
    sleep 2
    # NOTE: Do NOT mine kiro sessions here. Mining raw session JSON floods the
    # palace with tens of thousands of junk chunk-drawers (see jarvis-dream.sh,
    # 2026-07-25). Mining is owned by the nightly dream script, which mines the
    # vault (real markdown), not session transcripts.
    "$0" start
    ;;
  status)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "Running (PID $(cat "$PIDFILE"))"
    else
      echo "Not running"
      rm -f "$PIDFILE" 2>/dev/null
    fi
    ;;
  logs)
    tail -f "$LOGFILE"
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|logs}"
    ;;
esac
