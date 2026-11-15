#!/bin/bash
# Deploy FRIDAY (kiro-claw) to cappucino. Run from the macbook.
#
#   ./deploy-cappucino.sh              code + image rebuild + restart
#   ./deploy-cappucino.sh --no-build   code + restart only (no image rebuild)
#   ./deploy-cappucino.sh --sync-auth  also copy local data/kiro-data to cappucino
#
# systemd owns the bot on cappucino (User=yusuf, Restart=always). This script
# never starts the bot itself: it stops the running process and lets systemd
# bring it back, so no sudo is needed. It never overwrites cappucino's
# data/agent.json, and only touches data/kiro-data (auth tokens) with --sync-auth.
#
# Rollback: ssh cappucino 'docker tag kiro-claw-agent:rollback-<stamp> kiro-claw-agent:latest'
# then re-run with --no-build.
set -euo pipefail

REMOTE=cappucino
BUILD=1
SYNC_AUTH=0
for arg in "$@"; do
  case "$arg" in
    --no-build)  BUILD=0 ;;
    --sync-auth) SYNC_AUTH=1 ;;
    -h|--help)   sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$LOCAL_DIR"

echo "=== 1. Push main ==="
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "Uncommitted changes in tracked files; commit or stash first." >&2
  exit 1
fi
git push origin main
TARGET=$(git rev-parse --short HEAD)

if [ "$SYNC_AUTH" = 1 ]; then
  echo "=== Sync auth tokens (data/kiro-data) ==="
  rsync -az "$LOCAL_DIR/data/kiro-data/" "$REMOTE:Development/kiro-claw/data/kiro-data/"
fi

echo "=== 2. Deploy $TARGET on $REMOTE ==="
ssh "$REMOTE" BUILD="$BUILD" TARGET="$TARGET" bash -s <<'REMOTE_SCRIPT'
set -euo pipefail
cd ~/Development/kiro-claw

echo "--- Pull (fast-forward only) ---"
git pull --ff-only origin main
HEAD_NOW=$(git rev-parse --short HEAD)
if [ "$HEAD_NOW" != "$TARGET" ]; then
  echo "Remote is at $HEAD_NOW, expected $TARGET" >&2
  exit 1
fi

if [ "$BUILD" = 1 ]; then
  STAMP=$(date +%Y%m%d-%H%M)
  if docker image inspect kiro-claw-agent:latest >/dev/null 2>&1; then
    docker tag kiro-claw-agent:latest "kiro-claw-agent:rollback-$STAMP"
    echo "--- Tagged previous image as kiro-claw-agent:rollback-$STAMP ---"
  fi
  MP=$(grep -E '^MEMORY_PROVIDER=' .env 2>/dev/null | cut -d= -f2 || true)
  echo "--- Building image (MEMORY_PROVIDER=${MP:-memory}); slow on this CPU ---"
  docker build -q --build-arg MEMORY_PROVIDER="${MP:-memory}" \
    -t kiro-claw-agent:latest container/ > /dev/null
  echo "kiro-cli in image: $(docker run --rm --entrypoint kiro-cli kiro-claw-agent:latest --version 2>&1 | head -1)"
fi

echo "--- Restarting via systemd ---"
OLD=$(systemctl show -p MainPID --value kiro-claw)
docker kill kiroclaw-agent >/dev/null 2>&1 || true
if [ "$OLD" != "0" ]; then
  kill -TERM "$OLD" 2>/dev/null || true
  for _ in $(seq 1 20); do kill -0 "$OLD" 2>/dev/null || break; sleep 1; done
  kill -0 "$OLD" 2>/dev/null && kill -KILL "$OLD" || true
fi

for _ in $(seq 1 30); do
  NEW=$(systemctl show -p MainPID --value kiro-claw)
  if [ "$NEW" != "0" ] && [ "$NEW" != "$OLD" ] && systemctl is-active -q kiro-claw; then
    echo "kiro-claw active, PID $NEW, code $HEAD_NOW"
    exit 0
  fi
  sleep 2
done
echo "kiro-claw did not come back; check: journalctl -u kiro-claw -n 50" >&2
exit 1
REMOTE_SCRIPT

echo "=== Done. FRIDAY's container starts on the next message or event. ==="
