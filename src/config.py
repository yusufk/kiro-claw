import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TRIGGER_PATTERN = os.getenv("TRIGGER_PATTERN", "@jarvis")
KIRO_AGENT = os.getenv("KIRO_AGENT", "JARVIS")
CONTAINER_IMAGE = os.getenv("CONTAINER_IMAGE", "kiro-claw-agent:latest")
CONTAINER_TIMEOUT = int(os.getenv("CONTAINER_TIMEOUT", "300"))
BRAIN_DIR = os.getenv("BRAIN_DIR", "")
ALLOWED_CHAT_IDS: set[int] = {
    int(x.strip()) for x in os.getenv("ALLOWED_CHAT_IDS", "").split(",") if x.strip()
}
