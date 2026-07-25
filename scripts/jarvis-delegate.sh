#!/bin/bash
# jarvis-delegate.sh — JARVIS → FRIDAY task delegation
# Usage:
#   jarvis-delegate.sh "prompt text" [schedule]
#
# Schedule formats:
#   "once" ISO datetime  — e.g. "2026-07-13T18:00" (UTC)
#   "interval" minutes   — e.g. "30" (every 30 min)
#   "cron" expression    — e.g. "0 9 * * *"
#   (no schedule)        — immediate (next scheduler poll, ~30s)
#
# Examples:
#   jarvis-delegate.sh "Remind Yusuf to re-enable Hussein's messaging limits"
#   jarvis-delegate.sh "Check alarm status and report" "once" "2026-07-13T18:00"
#   jarvis-delegate.sh "Morning briefing" "cron" "0 5 * * *"

set -e

FRIDAY_DB="cappucino:/home/yusuf/Development/kiro-claw/data/kiro-claw.db"
CHAT_ID=72911340

PROMPT="$1"
SCHEDULE_TYPE="${2:-once}"
SCHEDULE_VALUE="${3:-$(date -u -v+1M '+%Y-%m-%dT%H:%M' 2>/dev/null || date -u -d '+1 min' '+%Y-%m-%dT%H:%M')}"

if [ -z "$PROMPT" ]; then
  echo "Usage: jarvis-delegate.sh \"prompt\" [schedule_type] [schedule_value]"
  echo ""
  echo "Schedule types: once, interval, cron"
  echo "Default: once, 1 minute from now"
  exit 1
fi

TASK_ID="task-j$(openssl rand -hex 4)"

# For 'once' tasks, next_run = schedule_value
if [ "$SCHEDULE_TYPE" = "once" ]; then
  NEXT_RUN="${SCHEDULE_VALUE}:00"
else
  NEXT_RUN="${SCHEDULE_VALUE}"
fi

CREATED=$(date -u '+%Y-%m-%dT%H:%M:%S+00:00')

ssh cappucino "python3 -c \"
import sqlite3
db = sqlite3.connect('/home/yusuf/Development/kiro-claw/data/kiro-claw.db')
db.execute('''INSERT INTO tasks (id, chat_id, prompt, schedule_type, schedule_value, next_run, status, created_at)
              VALUES (?, ?, ?, ?, ?, ?, 'active', ?)''',
           ('${TASK_ID}', ${CHAT_ID}, '''${PROMPT}''', '${SCHEDULE_TYPE}', '${SCHEDULE_VALUE}', '${NEXT_RUN}', '${CREATED}'))
db.commit()
db.close()
print('✅ Delegated to FRIDAY')
print(f'   Task: ${TASK_ID}')
print(f'   Type: ${SCHEDULE_TYPE}')
print(f'   When: ${SCHEDULE_VALUE}')
\""

echo "   Prompt: ${PROMPT}"
