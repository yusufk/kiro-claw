import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TRIGGER_PATTERN = os.getenv("TRIGGER_PATTERN", "@jarvis")
KIRO_AGENT = os.getenv("KIRO_AGENT", "JARVIS")
CONTAINER_IMAGE = os.getenv("CONTAINER_IMAGE", "kiro-claw-agent:latest")
CONTAINER_TIMEOUT = int(os.getenv("CONTAINER_TIMEOUT", "300"))
BRAIN_DIR = os.getenv("BRAIN_DIR", "")
EXTRA_HOSTS = os.getenv("EXTRA_HOSTS", "")  # e.g. "myserver:10.0.0.1,other:10.0.0.2"
PROJECTS = os.getenv("PROJECTS", "")  # e.g. "/Users/yusuf/Development/dha-slot-sniper,/Users/yusuf/Development/other"

# MCP secrets — passed as env vars to container, never written to files
MCP_SECRETS: dict[str, str] = {
    k: v for k, v in os.environ.items() if k.startswith("MCP_")
}  # e.g. "myserver:10.0.0.1,other:10.0.0.2"
ALLOWED_CHAT_IDS: set[int] = {
    int(x.strip()) for x in os.getenv("ALLOWED_CHAT_IDS", "").split(",") if x.strip()
}
