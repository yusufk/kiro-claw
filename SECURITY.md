# Security Policy

## NEVER commit these to git:

- `.env` — contains Telegram bot token, chat IDs, API keys
- `data/` — contains `agent.json` with MCP server credentials, `kiro-claw.db`, IPC files
- `data/kiro-data/` — contains kiro-cli auth tokens
- Any file containing tokens, passwords, API keys, or personal identifiers

## Sensitive values that must stay in .env only:

- `TELEGRAM_BOT_TOKEN`
- `ALLOWED_CHAT_IDS` (personal Telegram user IDs)
- `WEBHOOK_SECRET`
- `MCP_HOME_ASSISTANT_API_ACCESS_TOKEN`
- `MCP_MCP_OBSIDIAN_OBSIDIAN_API_KEY`
- `MCP_WORDPRESS_MASJID_WP_API_PASSWORD`
- `JARVIS_CHAT_ID`

## If you accidentally commit secrets:

1. `git filter-repo --path <file> --invert-paths --force`
2. `git remote add origin git@github.com:yusufk/kiro-claw.git`
3. `git push --force --all`
4. **Rotate ALL exposed credentials immediately** — history rewriting doesn't prevent cached copies

## Pre-commit check:

Before any commit, verify: `git diff --cached --name-only | grep -E '\.env|agent\.json|kiro-data'`
If any match — STOP and unstage them.
