# Kiro-Claw 🦞

A Telegram bot that bridges messages to [JARVIS](https://cli.kiro.dev/) (Kiro CLI agent) running inside a persistent Docker container.

## Architecture

```
Telegram ──→ python-telegram-bot ──→ per-chat async lock ──→ Docker container (persistent)
                                                                    │
                                                              kiro-cli chat
                                                              --resume (session continuity)
                                                              --agent JARVIS
                                                                    │
Telegram ←── typing loop + msg split ←── clean output ←── JSON stdout ←┘
```

**Key design decisions:**
- **Persistent container** — one long-running container, not one per message. Eliminates ~15s cold start overhead.
- **Session resume** — `kiro-cli chat --resume` maintains conversation context across messages.
- **stdin/stdout IPC** — JSON lines in, marker-delimited JSON out. No HTTP, no sockets.
- **Per-chat locking** — `asyncio.Lock` per chat ID serialises messages so the container handles one at a time.
- **Container isolation** — kiro-cli runs as unprivileged `node` user with read-only brain mount.

## Prerequisites

- Docker (Rancher Desktop, Docker Desktop, etc.)
- Python 3.11+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

## Setup

### 1. Install Kiro CLI

You need Kiro CLI installed on your host to create an agent configuration. The container uses its own Linux copy.

```bash
curl -fsSL https://cli.kiro.dev/install | bash
```

After install, create your agent:

```bash
# Create agent config directory
mkdir -p ~/.kiro/agents

# Create your agent (e.g. JARVIS)
cat > ~/.kiro/agents/jarvis.json << 'EOF'
{
  "name": "JARVIS",
  "description": "Your AI assistant",
  "prompt": "You are JARVIS, a sophisticated AI assistant."
}
EOF
```

### 2. Clone and install

```bash
git clone <repo-url> ~/Development/kiro-claw
cd ~/Development/kiro-claw
pip install -e .
```

### 3. Configure

```bash
cp .env.example .env
```

Edit `.env`:
```
TELEGRAM_BOT_TOKEN=<your-token-from-botfather>
TRIGGER_PATTERN=@jarvis
KIRO_AGENT=JARVIS
CONTAINER_IMAGE=kiro-claw-agent:latest
CONTAINER_TIMEOUT=300
ALLOWED_CHAT_IDS=<your-telegram-chat-id>
```

To find your chat ID, start the bot first and send `/chatid`.

### 4. Build the container

```bash
docker build -t kiro-claw-agent container/
```

This installs the Linux version of kiro-cli inside the container.

### 5. Authenticate kiro-cli (one-time)

```bash
mkdir -p data/kiro-data

docker run --rm -it \
  -v ~/.kiro:/home/node/.kiro:rw \
  -v ./data/kiro-data:/home/node/.local/share/kiro-cli:rw \
  --entrypoint kiro-cli \
  kiro-claw-agent:latest login --use-device-flow
```

This opens a device flow — follow the URL, enter the code, approve with your Builder ID. Auth tokens persist in `data/kiro-data/`.

Verify it worked:
```bash
docker run --rm -it \
  -v ~/.kiro:/home/node/.kiro:rw \
  -v ./data/kiro-data:/home/node/.local/share/kiro-cli:rw \
  --entrypoint kiro-cli \
  kiro-claw-agent:latest whoami
```

### 6. Run

Background (recommended):
```bash
./kiro-claw.sh start
./kiro-claw.sh logs     # watch output
```

Foreground:
```bash
kiro-claw
# or: python -m src.main
```

Management:
```bash
./kiro-claw.sh status   # check if running
./kiro-claw.sh stop     # stop bot + kill container
./kiro-claw.sh restart  # restart everything
```

## Bot Commands

| Command | Description |
|---------|-------------|
| `/ping` | Health check |
| `/chatid` | Show current chat ID |
| Any message (private chat) | Forwarded to JARVIS |
| `@jarvis <message>` (group chat) | Trigger prefix for groups |

## Container Mounts

| Host | Container | Mode | Purpose |
|------|-----------|------|---------|
| `~/.kiro/` | `/home/node/.kiro/` | rw | Agent config, MCP servers |
| `data/kiro-data/` | `/home/node/.local/share/kiro-cli/` | rw | Auth tokens |
| `~/Documents/Obsidian/.../AI brain/` | `/workspace/brain/` | ro | JARVIS memory context |

## IPC Protocol

**Host → Container** (stdin, one JSON per line):
```json
{"prompt": "Hello JARVIS", "agent": "JARVIS", "resume": true}
```

**Container → Host** (stdout, marker-delimited):
```
---KIROCLAW_OUTPUT_START---
{"status": "success", "result": "Good evening, Sir.", "error": null}
---KIROCLAW_OUTPUT_END---
```

## Response Times

| Event | Time |
|-------|------|
| Container startup (one-time) | ~1.5s |
| First message (kiro-cli cold) | ~45s |
| Subsequent messages (warm + resume) | ~11-18s |

The warm response floor is LLM processing time inside kiro-cli.
