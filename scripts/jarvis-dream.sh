#!/bin/bash
# JARVIS Dreaming Script — Nightly palace maintenance and sync
# 
# Architecture:
#   LOCAL: Mines Obsidian vault into local ChromaDB when MCP server is not running
#   REMOTE: Syncs vault to cappucino, mines into pgvector
#
# NOTE: Kiro session mining REMOVED (2026-07-25). The --extract general mode
#   on raw kiro-cli session JSON produces 60K+ junk drawers of chunked metadata
#   instead of meaningful conversation extracts. The palace should only contain
#   content deliberately filed via mempalace_add_drawer, diary entries, and
#   mined vault notes (which are actual markdown content).
#
# Schedule: launchd, 2AM daily

set -uo pipefail

MEMPALACE_CMD="/Users/yusuf/.pyenv/versions/3.12.8/bin/python -m mempalace.cli"
REMOTE_HOST="192.168.1.125"
REMOTE_VENV="/home/yusuf/.mempalace-venv/bin"
REMOTE_DSN="postgresql://jarvis:j4rv1s_p4l4ce_2026@localhost:5432/mempalace"
LOG="/Users/yusuf/.mempalace/dreaming.log"
VAULT_PATH="$HOME/Documents/Obsidian/Yusufs Vault"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

log "=== DREAMING BEGINS ==="

# --- LOCAL OPERATIONS (only if MCP server not holding palace lock) ---
if ! pgrep -f "mempalace.mcp_server" > /dev/null 2>&1; then
    log "Local palace unlocked — mining vault locally"

    $MEMPALACE_CMD mine "$VAULT_PATH" \
        --wing yusufs_vault --agent jarvis 2>> "$LOG" \
        && log "OK: local vault mined" \
        || log "WARN: local vault mine failed"

    $MEMPALACE_CMD sync --apply --wing yusufs_vault 2>> "$LOG" || true
    log "OK: local prune complete"
else
    log "INFO: local palace locked by MCP server — skipping local mine"
fi

# --- CHECK REMOTE CONNECTIVITY ---
if ! nc -z -w 3 "$REMOTE_HOST" 5432 2>/dev/null; then
    log "SKIP: cappucino not reachable — off LAN or postgres down"
    log "=== DREAMING COMPLETE (local only) ==="
    exit 0
fi

# --- SYNC VAULT TO CAPPUCINO ---
log "Syncing vault to cappucino..."

rsync -az --delete \
    "$VAULT_PATH/" cappucino:/tmp/vault-seed/ \
    --exclude='.obsidian' --exclude='.trash' --exclude='.git' 2>> "$LOG" \
    && log "OK: vault rsync" \
    || { log "FAIL: vault rsync — aborting remote"; log "=== DREAMING COMPLETE ==="; exit 1; }

# --- REMOTE MINING (stop MCP server → mine → restart) ---
log "Stopping remote MCP server for mining..."
ssh cappucino "systemctl --user stop mempalace-server.service" 2>> "$LOG"
sleep 2

# Mine vault (projects mode)
log "Mining vault into pgvector..."
ssh cappucino "MEMPALACE_BACKEND=pgvector MEMPALACE_PGVECTOR_DSN='$REMOTE_DSN' \
    $REMOTE_VENV/mempalace mine /tmp/vault-seed/ \
    --wing yusufs_vault --agent jarvis --backend pgvector" 2>> "$LOG" \
    && log "OK: remote vault mined" \
    || log "WARN: remote vault mine failed"

# Prune deleted/moved files
ssh cappucino "MEMPALACE_BACKEND=pgvector MEMPALACE_PGVECTOR_DSN='$REMOTE_DSN' \
    $REMOTE_VENV/mempalace sync --apply --wing yusufs_vault --backend pgvector" 2>> "$LOG" || true

# Restart MCP server
log "Restarting remote MCP server..."
ssh cappucino "systemctl --user start mempalace-server.service" 2>> "$LOG" \
    && log "OK: MCP server restarted" \
    || log "FAIL: MCP server failed to restart!"

# --- CLEANUP ---
ssh cappucino "rm -rf /tmp/vault-seed" 2>/dev/null

# --- TUNNEL & CONNECTION DISCOVERY (JARVIS reasoning) ---
# Invoke kiro-cli with a specific prompt to discover meaningful tunnels
# Only if MCP server is running (JARVIS needs mempalace tools)
if pgrep -f "mempalace.mcp_server" > /dev/null 2>&1; then
    log "JARVIS dreaming: discovering tunnels and connections..."

    DREAM_PROMPT="You are dreaming. This is an automated nightly task — no human is present.

Your job: discover and create meaningful cross-wing tunnels in the MemPalace.

Steps:
1. Run mempalace_list_wings to see what wings exist
2. For each wing pair, run mempalace_search with a representative query from one wing and see if results appear in another wing
3. Where strong semantic connections exist (distance < 0.5), create a tunnel with mempalace_create_tunnel
4. Check mempalace_list_tunnels first to avoid duplicates
5. Create at most 5 new tunnels per run
6. Write a brief diary entry with mempalace_diary_write summarising what you discovered

Be selective — only create tunnels where the connection is genuinely meaningful, not just superficial keyword overlap."

    echo "$DREAM_PROMPT" | /Users/yusuf/.local/bin/kiro-cli chat --agent jarvis --non-interactive 2>> "$LOG" \
        && log "OK: JARVIS dream session complete" \
        || log "WARN: JARVIS dream session failed"
else
    log "SKIP: MCP server not running — no tunnel discovery this cycle"
fi

log "=== DREAMING COMPLETE ==="
