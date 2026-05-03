#!/bin/bash
# Deploy FRIDAY (kiro-claw) to cappucino — run from macbook
# One SSH session for file transfer, one for setup. That's it.
set -euo pipefail

REMOTE=cappucino
REMOTE_DIR="~/Development/kiro-claw"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Step 1: Push latest code to GitHub ==="
cd "$LOCAL_DIR"
git push origin main 2>/dev/null || git push origin master 2>/dev/null || echo "Push failed or nothing to push"

echo ""
echo "=== Step 2: Copy kiro-data (auth tokens) to cappucino ==="
rsync -az --progress "$LOCAL_DIR/data/kiro-data/" "$REMOTE:Development/kiro-claw/data/kiro-data/"

echo ""
echo "=== Step 3: Copy agent.json to cappucino ==="
rsync -az "$LOCAL_DIR/data/agent.json" "$REMOTE:Development/kiro-claw/data/agent.json"

echo ""
echo "=== Step 4: Setup and launch on cappucino ==="
ssh "$REMOTE" bash -s <<'REMOTE_SCRIPT'
set -euo pipefail
cd ~/Development/kiro-claw

echo "--- Pulling latest code ---"
git pull --ff-only origin main 2>/dev/null || git pull --ff-only origin master 2>/dev/null || echo "Already up to date"

echo "--- Creating data dirs ---"
mkdir -p data/ipc data/scratch data/kiro-data

echo "--- Setting up Python venv ---"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q python-telegram-bot[ext] python-dotenv croniter aiohttp

echo "--- Building Docker image ---"
cd container
docker build -t kiro-claw-agent:latest .
cd ..

echo "--- Fixing kiro-claw.sh for Linux ---"
sed -i 's|/Users/yusuf/.pyenv/shims/python3|.venv/bin/python3|g' kiro-claw.sh
sed -i 's|/Users/yusuf/.pyenv/versions/3.12.8/bin/python|.venv/bin/python3|g' kiro-claw.sh

echo "--- Stopping any existing instance ---"
./kiro-claw.sh stop 2>/dev/null || true
docker kill kiroclaw-agent 2>/dev/null || true

echo "--- Starting FRIDAY ---"
./kiro-claw.sh start

sleep 3
./kiro-claw.sh status
echo ""
echo "=== FRIDAY deployed on cappucino ==="
echo "Logs: ssh cappucino 'cd ~/Development/kiro-claw && ./kiro-claw.sh logs'"
REMOTE_SCRIPT
