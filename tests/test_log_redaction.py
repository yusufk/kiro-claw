"""Bot token must never appear in kiro-claw logs."""
import logging

import src.main  # noqa: F401  (installs logger levels + redaction filter)
from src.main import RedactTokenFilter

# Built at runtime so no token-shaped literal sits in the repo (keeps secret scanners quiet).
FAKE = "bot" + "1" * 9 + ":" + "AAFakeToken" + "x" * 24


def _render(msg, *args):
    rec = logging.LogRecord("httpx", logging.WARNING, __file__, 1, msg, args, None)
    RedactTokenFilter().filter(rec)
    return rec.getMessage()


def test_httpx_info_is_silenced():
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_token_redacted_in_plain_message():
    out = _render(f"POST https://api.telegram.org/{FAKE}/sendMessage failed")
    assert "AAFakeToken" not in out
    assert "bot<redacted>/sendMessage" in out


def test_token_redacted_in_formatted_args():
    out = _render('HTTP Request: %s %s "%s"', "POST", f"https://api.telegram.org/{FAKE}/getMe", "200")
    assert "AAFakeToken" not in out


def test_ordinary_messages_untouched():
    assert _render("IPC message sent to %s", 42) == "IPC message sent to 42"
