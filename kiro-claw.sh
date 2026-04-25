#!/bin/bash
# Kiro-Claw launcher — runs the Telegram bot as a background process
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$DIR/data/kiro-claw.pid"
LOGFILE="$DIR/data/kiro-claw.log"

mkdir -p "$DIR/data"

# Ensure agent.json exists (copy from template if missing)
if [ ! -f "$DIR/data/agent.json" ]; then
  cp "$DIR/agent.json.example" "$DIR/data/agent.json"
  echo "Created data/agent.json from template — edit secrets or ensure .env has them"
fi

case "${1:-start}" in
  start)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "Already running (PID $(cat "$PIDFILE"))"
      exit 0
    fi
    echo "Starting Kiro-Claw..."
    cd "$DIR"
    nohup /Users/yusuf/.pyenv/shims/python3 -m src.main >> "$LOGFILE" 2>&1 &
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
    # Mine kiro-claw container sessions into MemPalace before restarting
    /Users/yusuf/.pyenv/versions/3.12.8/bin/python -m mempalace mine \
      "$DIR/data/kiro-data" --mode convos --wing friday_sessions --agent friday 2>/dev/null || true
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
