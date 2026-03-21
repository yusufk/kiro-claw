# Kiro-Claw 🦞

A Telegram bot that bridges messages to [JARVIS](https://cli.kiro.dev/) (Kiro CLI agent) running inside a persistent Docker container. Includes task scheduling, proactive messaging, and live response streaming.

## Architecture

```
Telegram ──→ python-telegram-bot ──→ per-chat async lock ──→ Docker container (persistent)
                                                                    │
                                                              kiro-cli chat
                                                              --resume (session continuity)
                                                              --agent JARVIS
                                                                    │
Telegram ←── streaming drafts + msg split ←── redact secrets ←── clean output ←── JSON stdout ←┘

Background loops:
  Scheduler ──→ polls SQLite for due tasks ──→ runs prompt in container ──→ sends result to Telegram
  IPC Watcher ──→ polls data/ipc/ for JSON files ──→ sends messages / creates tasks
```

**Key design decisions:**
- **Persistent container** — one long-running container, not one per message. Eliminates ~15s cold start overhead.
- **Session resume** — `kiro-cli chat --resume` maintains conversation context across messages.
- **stdin/stdout IPC** — JSON lines in, marker-delimited JSON out. No HTTP, no sockets.
- **Per-chat locking** — `asyncio.Lock` per chat ID serialises messages so the container handles one at a time.
- **Container isolation** — kiro-cli runs as unprivileged `node` user. Agent config is separate from host `~/.kiro`.
- **Secret redaction** — all MCP secret values are pattern-matched and stripped from output before delivery to Telegram.
- **Streaming** — `sendMessageDraft` (Bot API 9.3) streams partial responses live in Telegram.
- **File-based IPC** — container writes JSON to `/workspace/ipc/` for proactive messaging and task scheduling.

## Quick Start

```bash
git clone https://github.com/yusufk/kiro-claw.git
cd kiro-claw
pip install -e .
cp .env.example .env        # edit with your tokens
docker build -t kiro-claw-agent container/
./kiro-claw.sh start        # start the bot
./kiro-claw.sh logs         # watch output
```

## Management

```bash
./kiro-claw.sh start        # start bot (background, with PID file)
./kiro-claw.sh stop         # stop bot + kill agent container
./kiro-claw.sh restart      # restart everything
./kiro-claw.sh status       # check if running
./kiro-claw.sh logs         # tail the log file
```

Logs: `data/kiro-claw.log` | PID: `data/kiro-claw.pid`

## Bot Commands

| Command | Description |
|---------|-------------|
| `/ping` | Health check |
| `/chatid` | Show current chat ID |
| `/tasks` | List active scheduled tasks |
| `/cancel <task_id>` | Cancel a scheduled task |
| Any message (private chat) | Forwarded to JARVIS agent |
| `@jarvis <message>` (group) | Trigger prefix for group chats |

## Task Scheduling

Scheduling is handled entirely by the AI agent — no rigid command syntax needed. Just talk naturally:

- "wake me up in 10 minutes"
- "remind me to check the server every hour"
- "every morning at 9am give me a briefing"

JARVIS understands the intent, computes the time, and calls the `jarvis-schedule` IPC tool. Tasks are stored in SQLite (`data/tasks.db`) and polled every 30 seconds. Use `/tasks` to see what's scheduled and `/cancel` to remove one.

## Proactive Messaging (Container → Telegram)

The container agent can initiate messages and schedule tasks by writing JSON files to `/workspace/ipc/` (mounted from `data/ipc/`). The host polls this directory every 2 seconds.

### Container IPC tools (available in PATH)

```bash
# Send a message to Telegram
jarvis-send $JARVIS_CHAT_ID "Sir, the backup completed successfully."

# Schedule a task
jarvis-schedule $JARVIS_CHAT_ID cron "0 9 * * *" "Good morning briefing"
jarvis-schedule $JARVIS_CHAT_ID interval 3600000 "Hourly system check"
jarvis-schedule $JARVIS_CHAT_ID once "2026-03-22T10:00" "Remind about meeting"

# Cancel a task
jarvis-cancel-task <task_id>
```

`$JARVIS_CHAT_ID` is set automatically from the incoming message's chat ID.

### IPC JSON format (for reference)

```json
{"type": "message", "chat_id": 12345, "text": "Hello from JARVIS"}
{"type": "schedule_task", "chat_id": 12345, "prompt": "Check health", "schedule_type": "cron", "schedule_value": "0 9 * * *"}
{"type": "cancel_task", "task_id": "task-abc123"}
```

## Security Model

Secrets never reach Telegram, enforced at three layers:

1. **Isolated agent config** — container gets `data/agent.json` (gitignored), not `~/.kiro`. Secrets use `__ENV:VAR__` placeholders resolved at startup from container env vars.
2. **Prompt instruction** — container agent prompt includes a rule to never output credentials.
3. **Output redaction** — `_clean()` in `runner.py` pattern-matches all `MCP_*` secret values from `.env` and replaces them with `[REDACTED]` before anything is sent to Telegram.
4. **Env file injection** — secrets passed via `--env-file` (temp file, 0600 perms, deleted after container ready). Never visible in `ps aux`.

## Setup Details

### Prerequisites

- Docker (Rancher Desktop recommended)
- Python 3.11+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

### Configuration

Edit `.env` (see `.env.example`):

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `TRIGGER_PATTERN` | Trigger word for group chats (default: `@jarvis`) |
| `KIRO_AGENT` | Agent name (default: `JARVIS`) |
| `CONTAINER_IMAGE` | Agent container image (default: `kiro-claw-agent:latest`) |
| `CONTAINER_TIMEOUT` | Max response time in seconds (default: `300`) |
| `ALLOWED_CHAT_IDS` | Comma-separated allowed Telegram chat IDs |
| `BRAIN_DIR` | Path to shared brain/memory directory |
| `EXTRA_HOSTS` | LAN DNS entries: `hostname:ip,hostname:ip` |
| `MCP_*` | MCP server secrets injected into container |

### Build the agent container

```bash
docker build -t kiro-claw-agent container/
```

### Authenticate kiro-cli (one-time)

```bash
docker run --rm -it \
  -v ./data/agent.json:/home/node/.kiro/agents/JARVIS.json:rw \
  -v ./data/kiro-data:/home/node/.local/share/kiro-cli:rw \
  --entrypoint kiro-cli \
  kiro-claw-agent:latest login --use-device-flow
```

## Container Mounts

| Host | Container | Mode | Purpose |
|------|-----------|------|---------|
| `data/agent.json` | `/home/node/.kiro/agents/JARVIS.json` | rw | Agent config |
| `data/kiro-data/` | `/home/node/.local/share/kiro-cli/` | rw | Auth tokens |
| `BRAIN_DIR` | `/workspace/brain/` | rw | Shared memory |
| `data/ipc/` | `/workspace/ipc/` | rw | Proactive messaging IPC |

## Response Times

| Event | Time |
|-------|------|
| Container startup (one-time) | ~1.5s |
| First message (kiro-cli cold) | ~45s |
| Subsequent messages (warm + resume) | ~11-18s |

## Project Structure

```
kiro-claw/
├── kiro-claw.sh              # Start/stop/status management script
├── .env                      # Configuration (gitignored)
├── pyproject.toml             # Python package config
├── src/
│   ├── main.py               # Entry point — wires bot + scheduler + IPC
│   ├── bot.py                # Telegram bot handlers + /remind /tasks /cancel
│   ├── runner.py             # Docker container lifecycle + streaming
│   ├── queue.py              # Per-chat async locking
│   ├── config.py             # Environment config loader
│   ├── scheduler.py          # Task scheduler (SQLite + asyncio poll loop)
│   ├── ipc.py                # IPC watcher (polls data/ipc/ for JSON files)
│   └── db.py                 # SQLite task database
├── container/
│   ├── Dockerfile            # Agent container (node + kiro-cli + tools)
│   ├── entrypoint.py         # Container stdin loop + kiro-cli invocation
│   └── tools/                # IPC shell scripts for container agent
│       ├── jarvis-send       # Send proactive Telegram message
│       ├── jarvis-schedule   # Create scheduled task
│       └── jarvis-cancel-task # Cancel a task
├── data/                     # Runtime data (gitignored)
│   ├── agent.json            # Container agent config
│   ├── kiro-data/            # kiro-cli auth tokens
│   ├── tasks.db              # Scheduled tasks database
│   ├── ipc/                  # IPC message queue directory
│   ├── kiro-claw.log         # Bot log file
│   └── kiro-claw.pid         # Bot PID file
└── README.md
```
