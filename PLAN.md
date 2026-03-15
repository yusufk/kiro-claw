# Kiro-Claw: Telegram ↔ JARVIS Bridge (Containerised)

A Python bridge that connects Telegram to `kiro-cli` running inside Docker containers.

## Status: ✅ END-TO-END WORKING (2026-03-15 19:35 SAST)

## Architecture

```
Telegram → python-telegram-bot → per-chat async lock → Docker container (kiro-cli) → response → Telegram
```

## What's Done

### Host Side
- `src/config.py` — loads .env (token, trigger, allowed chats, container image, timeout)
- `src/runner.py` — spawns Docker container, pipes JSON stdin, parses stdout markers
- `src/queue.py` — per-chat asyncio.Lock serialises container invocations
- `src/bot.py` — Telegram bot (/ping, /chatid, trigger matching, typing, 4096-char splitting)
- `src/main.py` — entry point wiring bot → queue → runner

### Container Side
- `container/Dockerfile` — node:22-slim + curl + python3 + unzip + kiro-cli Linux
- `container/entrypoint.py` — reads JSON stdin, runs kiro-cli, outputs JSON with markers

### Config
- `.env` — bot token, JARVIS agent, allowed chat IDs
- `data/kiro-data/data.sqlite3` — Builder ID auth tokens (device flow)

## Key Learnings / Gotchas
1. kiro-cli installer needs `unzip` package
2. Installer puts binaries in `/root/.local/bin/` — must `mv kiro-cli* /usr/local/bin/`
3. Agent name is case-sensitive: `JARVIS` not `jarvis`
4. `.kiro` dir must be mounted **rw** (writes history/state)
5. `kiro-data` dir must be mounted **rw** (token refresh)
6. Auth via `kiro-cli login --use-device-flow` inside container
7. Response time ~55s (container cold start + kiro-cli processing)

## Container Mounts
| Host Path | Container Path | Mode | Purpose |
|-----------|---------------|------|---------|
| `~/.kiro/` | `/home/node/.kiro/` | rw | Agent config, MCP servers |
| `data/kiro-data/` | `/home/node/.local/share/kiro-cli/` | rw | Auth tokens |
| `~/Documents/Obsidian/.../AI brain/` | `/workspace/brain/` | ro | JARVIS memory |
| Per-chat tmpdir | `/workspace/scratch/` | rw | Scratch space |

## TODO
- [ ] `git init` + push to GitHub
- [ ] Optimise response time (container reuse / pre-warming)
- [ ] Deploy to cappucino for always-on
- [ ] Add `data/` to `.gitignore`
- [ ] Typing indicator loop during container processing
- [ ] Strip ANSI escape codes from kiro-cli output
- [ ] Error handling for container build failures
