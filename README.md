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
Telegram ←── typing loop + msg split ←── redact secrets ←── clean output ←── JSON stdout ←┘
```

**Key design decisions:**
- **Persistent container** — one long-running container, not one per message. Eliminates ~15s cold start overhead.
- **Session resume** — `kiro-cli chat --resume` maintains conversation context across messages.
- **stdin/stdout IPC** — JSON lines in, marker-delimited JSON out. No HTTP, no sockets.
- **Per-chat locking** — `asyncio.Lock` per chat ID serialises messages so the container handles one at a time.
- **Container isolation** — kiro-cli runs as unprivileged `node` user. Agent config is separate from host `~/.kiro`.
- **Secret redaction** — all MCP secret values are pattern-matched and stripped from output before delivery to Telegram.

## Security Model

Secrets never reach Telegram, enforced at three layers:

1. **Isolated agent config** — container gets `data/agent.json` (gitignored), not `~/.kiro`. Secrets use `__ENV:VAR__` placeholders resolved at startup from container env vars.
2. **Prompt instruction** — container agent prompt includes a rule to never output credentials.
3. **Output redaction** — `_clean()` in `runner.py` pattern-matches all `MCP_*` secret values from `.env` and replaces them with `[REDACTED]` before anything is sent to Telegram.

## Prerequisites

- Docker (Rancher Desktop, Docker Desktop, etc.)
- Python 3.11+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

## Setup

### 1. Install Kiro CLI

You need Kiro CLI on your host to create an agent configuration. The container uses its own Linux copy.

```bash
curl -fsSL https://cli.kiro.dev/install | bash
```

### 2. Clone and install

```bash
git clone https://github.com/yusufk/kiro-claw.git
cd kiro-claw
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
BRAIN_DIR=<path-to-brain-directory>
EXTRA_HOSTS=<hostname:ip,hostname:ip>

# MCP secrets — injected as env vars into container
MCP_HOME_ASSISTANT_API_ACCESS_TOKEN=<your-ha-token>
MCP_MCP_OBSIDIAN_OBSIDIAN_API_KEY=<your-obsidian-key>
```

To find your chat ID, start the bot and send `/chatid`.

### 4. Create the container agent config

Generate `data/agent.json` from your host agent config with secrets replaced by `__ENV:VAR__` placeholders:

```bash
mkdir -p data
python3 -c "
import json, re
d = json.load(open('$HOME/.kiro/agents/jarvis.json'))
SECRET_WORDS = {'TOKEN','KEY','PASSWORD','SECRET','AUTH','CREDENTIAL'}
for srv_name, srv in d.get('mcpServers', {}).items():
    for k in list(srv.get('env', {}).keys()):
        v = srv['env'][k]
        if any(w in k.upper() for w in SECRET_WORDS):
            srv['env'][k] = f'__ENV:MCP_{srv_name.upper().replace(chr(45),chr(95))}_{k}__'
        elif isinstance(v, str) and v.startswith('/Users/'):
            srv['env'][k] = f'/workspace/brain/{v.split(chr(47))[-1]}'
d['resources'] = ['file:///workspace/brain/**/*.md']
json.dump(d, open('data/agent.json', 'w'), indent=2)
print('Created data/agent.json')
"
```

The entrypoint resolves `__ENV:VAR__` placeholders from container environment variables at startup.

### 5. Build the container

```bash
docker build -t kiro-claw-agent container/
```

### 6. Authenticate kiro-cli (one-time)

```bash
mkdir -p data/kiro-data

docker run --rm -it \
  -v ./data/agent.json:/home/node/.kiro/agents/JARVIS.json:rw \
  -v ./data/kiro-data:/home/node/.local/share/kiro-cli:rw \
  --entrypoint kiro-cli \
  kiro-claw-agent:latest login --use-device-flow
```

Follow the device flow URL and approve with your Builder ID. Auth tokens persist in `data/kiro-data/`.

### 7. Run

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

## Configuration

### EXTRA_HOSTS

Route container traffic to LAN services without `--network host`:

```
EXTRA_HOSTS=myserver:192.168.1.100,otherhost:10.0.0.5
```

Each entry becomes a `--add-host` flag on the container, adding DNS entries to `/etc/hosts`.

### BRAIN_DIR

Mount a directory into the container at `/workspace/brain/` for shared context:

```
BRAIN_DIR=/path/to/your/brain/directory
```

Mounted read-write so the container agent can update shared memory files.

## Bot Commands

| Command | Description |
|---------|-------------|
| `/ping` | Health check |
| `/chatid` | Show current chat ID |
| Any message (private chat) | Forwarded to agent |
| `@jarvis <message>` (group chat) | Trigger prefix for groups |

## Container Mounts

| Host | Container | Mode | Purpose |
|------|-----------|------|---------|
| `data/agent.json` | `/home/node/.kiro/agents/JARVIS.json` | rw | Agent config (secrets as placeholders) |
| `data/kiro-data/` | `/home/node/.local/share/kiro-cli/` | rw | Auth tokens |
| `BRAIN_DIR` | `/workspace/brain/` | rw | Shared memory and context |

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
