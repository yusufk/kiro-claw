#!/bin/bash
# JARVIS Federated Brain Sync Script

BRAIN_DIR=~/Documents/Obsidian/Yusufs\ Vault/AI\ brain
HOSTNAME=$(hostname -s)

cd "$BRAIN_DIR" || exit 1

# Pull latest changes
echo "🧠 Syncing JARVIS brain from remote..."
git pull --rebase

# Stage all changes
git add *.md memory.json data/

# Check if there are changes to commit
if git diff --staged --quiet; then
    echo "✓ No changes to sync"
else
    # Commit with node identifier
    git commit -m "JARVIS sync from $HOSTNAME - $(date +%Y-%m-%d\ %H:%M)"
    
    # Push to remote
    git push
    echo "✓ Brain synced successfully"
fi
