# Container Tools

Shell scripts available in the container's PATH at `/usr/local/bin/`. These are the primary way JARVIS interacts with the host bot process via the IPC directory (`/workspace/ipc/`).

## Adding New Tools

1. Create a shell script in `container/tools/`
2. Make it executable: `chmod +x container/tools/your-tool`
3. Rebuild the container: `docker build -t kiro-claw-agent container/`
4. Restart: `docker kill kiroclaw-agent && ./kiro-claw.sh restart`

Tools should write JSON files to `/workspace/ipc/` for the host to process. The host polls this directory every 2 seconds.

## Available Tools

### jarvis-send
Send a message to Telegram.
```bash
jarvis-send <chat_id> <message>
```

### jarvis-schedule
Create a scheduled task.
```bash
jarvis-schedule <chat_id> <type> <value> <prompt>
# Types: cron | interval | once
jarvis-schedule $JARVIS_CHAT_ID cron "0 9 * * *" "Morning briefing"
jarvis-schedule $JARVIS_CHAT_ID interval 3600000 "Hourly check"
jarvis-schedule $JARVIS_CHAT_ID once "2026-04-10T10:00" "Reminder"
```

### jarvis-cancel-task
Cancel a scheduled task.
```bash
jarvis-cancel-task <task_id>
```

### jarvis-photo
Grab a camera snapshot from Home Assistant and send to Telegram.
```bash
jarvis-photo <chat_id> <camera_entity_id> [caption]
jarvis-photo $JARVIS_CHAT_ID camera.network_video_recorder_channel_7 "Driveway check"
```
Requires `MCP_HOME_ASSISTANT_API_ACCESS_TOKEN` env var and HA accessible at `http://cappucino:8123`.

## IPC JSON Types

The host processes these JSON types from `/workspace/ipc/*.json`:

| Type | Fields | Description |
|------|--------|-------------|
| `message` | `chat_id`, `text` | Send text to Telegram |
| `photo` | `chat_id`, `path`, `caption` | Send photo to Telegram |
| `schedule_task` | `chat_id`, `prompt`, `schedule_type`, `schedule_value` | Create task |
| `cancel_task` | `task_id` | Cancel task |
