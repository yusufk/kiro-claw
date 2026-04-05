# Kiro-Claw 🦞

A Telegram bot that bridges messages to [JARVIS](https://cli.kiro.dev/) (Kiro CLI agent) running inside a persistent Docker container. Includes task scheduling, proactive messaging, event ingestion, and live response streaming.

## Architecture

```
                          ┌─────────────────────────────────────────────┐
                          │              Kiro-Claw Host                 │
                          │                                             │
  Telegram ──────────────►│  python-telegram-bot                        │
                          │       │                                     │
                          │       ▼                                     │
                          │  per-chat async lock ──► Docker container   │
                          │                          (persistent)       │
                          │                          kiro-cli --resume  │
                          │                               │             │
  Telegram ◄──────────────│  streaming drafts ◄── clean ◄─┘             │
                          │                                             │
                          │  ┌─────────────────────────────────────┐    │
                          │  │         SQLite (kiro-claw.db)       │    │
                          │  │  ┌──────────┬──────────┬──────────┐ │    │
                          │  │  │ messages │  events  │  tasks   │ │    │
                          │  │  └──────────┴──────────┴──────────┘ │    │
                          │  └─────────────────────────────────────┘    │
                          │       ▲            ▲            ▲           │
                          │       │            │            │           │
                          │  Bot handler  Webhook:8099  Scheduler      │
                          │  (stores all  (POST /event) (polls 30s)    │
                          │   messages)        │                       │
                          │                    ▼                       │
                          │              Event processor               │
                          │              (polls 5s → Telegram)         │
                          │                                             │
                          │  IPC watcher (polls data/ipc/ every 2s)    │
                          └─────────────────────────────────────────────┘
                                               ▲
                                               │ POST /event
                                               │
                          ┌────────────────────────────────────────────┐
                          │  Home Assistant (cappucino)                │
                          │  rest_command → http://macbook:8099/event  │
                          └────────────────────────────────────────────┘
```

### Data Flow

1. **Telegram → JARVIS**: Messages arrive via polling, get queued per-chat, forwarded to the persistent Docker container running `kiro-cli --resume`. Responses stream back via `sendMessageDraft`.
2. **External events → Telegram**: HA (or any source) POSTs to `/event` on port 8099. Events are stored in SQLite and the event processor forwards them to Telegram within 5 seconds.
3. **Scheduled tasks**: Stored in SQLite, polled every 30s. Due tasks run their prompt through the container and send results to Telegram.
4. **Container → Telegram**: The container writes JSON files to `/workspace/ipc/` for proactive messaging and task management.
5. **Message history**: All Telegram messages (user, bot, group observations) are persisted in SQLite for conversation context.

### Key Design Decisions

- **Persistent container** — one long-running container, not one per message. Eliminates ~15s cold start overhead.
- **Session resume** — `kiro-cli chat --resume` maintains conversation context across messages.
- **Unified SQLite DB** — messages, events, and tasks in one database (`data/kiro-claw.db`). Inspired by [nanoclaw](https://github.com/yusufk/nanoclaw).
- **Transport-agnostic event bus** — the `events` table doesn't care how events arrive (webhook, MQTT, IPC). Processors just poll the table.
- **Per-chat locking** — `asyncio.Lock` per chat ID serialises messages so the container handles one at a time.
- **Secret redaction** — all MCP secret values are pattern-matched and stripped from output before delivery to Telegram.
- **Streaming** — `sendMessageDraft` (Bot API 9.3) streams partial responses live in Telegram.

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

## Bot Commands

| Command | Description |
|---------|-------------|
| `/ping` | Health check |
| `/chatid` | Show current chat ID |
| `/tasks` | List active scheduled tasks |
| `/cancel <task_id>` | Cancel a scheduled task |
| Any message (private chat) | Forwarded to JARVIS agent |
| `@jarvis <message>` (group) | Trigger prefix for group chats |

## Event Webhook

Kiro-Claw runs an HTTP server on port **8099** that accepts events from external sources. Events are routed through the JARVIS container for intelligent processing — not just forwarded as notifications.

JARVIS decides what to do: ignore routine events, alert you about important ones, check cameras, arm the alarm, etc.

### Security

The webhook requires both:
- **IP allowlist** — only accepts requests from configured IPs (default: `127.0.0.1,192.168.1.125`)
- **Bearer token** — `Authorization: Bearer <secret>` header required if `WEBHOOK_SECRET` is set

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/event` | Ingest an event (auth required) |
| `GET` | `/health` | Health check (no auth) |

### Event Format

```bash
curl -X POST http://macbook:8099/event \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_WEBHOOK_SECRET" \
  -d '{
    "source": "ha",
    "event_type": "state_changed",
    "data": {
      "entity_id": "binary_sensor.driveway_motion",
      "state": "on",
      "friendly_name": "Driveway Motion"
    }
  }'
```

### How JARVIS Handles Events

Events are batched (10s window to group rapid-fire triggers), then sent to the JARVIS container as a prompt. JARVIS:
- Ignores routine events (daytime motion, expected state changes)
- Alerts you about important events (late-night motion, alarm triggers, doorbell)
- Takes action when possible (checks cameras, arms alarm, turns on lights)
- Correlates multiple events into a single assessment

### Home Assistant Integration

Two things are needed on the HA side:

**1. Add `rest_command` to `configuration.yaml`:**

This creates a reusable service that any automation can call to send events to JARVIS.

```yaml
rest_command:
  jarvis_event:
    url: "http://<MACBOOK_IP>:8099/event"
    method: POST
    headers:
      Authorization: "Bearer YOUR_WEBHOOK_SECRET"
    content_type: "application/json"
    payload: >-
      {"source":"ha","event_type":"{{ event_type }}","data":{"entity_id":"{{ entity_id }}","state":"{{ state }}","friendly_name":"{{ friendly_name }}"}}
```

After adding, restart HA core (`ha core restart`) — a reload won't pick up new integrations.

**2. Add `rest_command.jarvis_event` to your automations:**

Add it as an action in any automation that should notify JARVIS. It works alongside existing Telegram notifications — add it as the first action so JARVIS gets the event immediately:

```yaml
# Example: existing motion automation
- id: movement_driveway
  alias: Movement - driveway
  actions:
    # JARVIS event (add this)
    - action: rest_command.jarvis_event
      data:
        event_type: state_changed
        entity_id: movement_driveway
        state: "There's movement in the driveway"
        friendly_name: Movement - driveway
    # Existing Telegram notification (keep this)
    - action: telegram_bot.send_message
      data:
        message: "There's movement in the driveway"
```

You can also update automations via the HA API:

```bash
curl -X POST -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/json" \
  "http://localhost:8123/api/config/automation/config/<automation_id>" \
  -d @automation.json
```

Then reload: `curl -X POST -H "Authorization: Bearer $HA_TOKEN" http://localhost:8123/api/services/automation/reload`

## Task Scheduling

Scheduling is handled by the AI agent — just talk naturally:

- "wake me up in 10 minutes"
- "remind me to check the server every hour"
- "every morning at 9am give me a briefing"

JARVIS understands the intent and calls the `jarvis-schedule` IPC tool. Tasks are stored in SQLite and polled every 30 seconds.

## Proactive Messaging (Container → Telegram)

The container agent can initiate messages and schedule tasks by writing JSON files to `/workspace/ipc/` (mounted from `data/ipc/`). The host polls this directory every 2 seconds.

### Container IPC tools (available in PATH)

```bash
jarvis-send $JARVIS_CHAT_ID "Sir, the backup completed successfully."
jarvis-schedule $JARVIS_CHAT_ID cron "0 9 * * *" "Good morning briefing"
jarvis-cancel-task <task_id>
```

## Security Model

Secrets never reach Telegram, enforced at three layers:

1. **Isolated agent config** — container gets `data/agent.json` (gitignored), not `~/.kiro`. Secrets use `__ENV:VAR__` placeholders resolved at startup.
2. **Output redaction** — `runner.py` pattern-matches all `MCP_*` secret values and replaces them with `[REDACTED]`.
3. **Env file injection** — secrets passed via `--env-file` (temp file, 0600 perms, deleted after container ready). Never visible in `ps aux`.

## Configuration

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
| `PROJECTS` | Comma-separated project paths to mount at `/workspace/projects/` |
| `EXTRA_HOSTS` | LAN DNS entries: `hostname:ip,hostname:ip` |
| `WEBHOOK_SECRET` | Bearer token for webhook auth |
| `WEBHOOK_ALLOWED_IPS` | Comma-separated IPs allowed to POST events (default: `127.0.0.1,192.168.1.125`) |
| `WEBHOOK_PORT` | Webhook listen port (default: `8099`) |
| `MCP_*` | MCP server secrets injected into container |

## Project Structure

```
kiro-claw/
├── kiro-claw.sh              # Start/stop/status management
├── pyproject.toml
├── src/
│   ├── main.py               # Entry point — wires bot + scheduler + IPC + webhook + events
│   ├── bot.py                # Telegram handlers, message storage
│   ├── runner.py             # Docker container lifecycle + streaming
│   ├── queue.py              # Per-chat async locking
│   ├── config.py             # Environment config loader
│   ├── db.py                 # SQLite: messages, events, tasks
│   ├── scheduler.py          # Task scheduler (polls every 30s)
│   ├── ipc.py                # IPC watcher (polls data/ipc/ every 2s)
│   ├── webhook.py            # HTTP server on :8099 for event ingestion
│   └── events.py             # Event processor (polls events table every 5s)
├── container/
│   ├── Dockerfile
│   ├── entrypoint.py
│   └── tools/
│       ├── jarvis-send
│       ├── jarvis-schedule
│       └── jarvis-cancel-task
├── data/                     # Runtime data (gitignored)
│   ├── kiro-claw.db          # Unified SQLite database
│   ├── agent.json            # Container agent config
│   ├── kiro-data/            # kiro-cli auth tokens
│   ├── ipc/                  # IPC message queue
│   ├── kiro-claw.log
│   └── kiro-claw.pid
└── README.md
```

## Response Times

| Event | Time |
|-------|------|
| Container startup (one-time) | ~1.5s |
| First message (kiro-cli cold) | ~45s |
| Subsequent messages (warm + resume) | ~11-18s |
| Webhook event → Telegram | <5s |
