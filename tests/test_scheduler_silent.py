"""Scheduled tasks must not forward container stdout to Telegram."""

import pytest
from unittest.mock import AsyncMock, patch

from src import scheduler


@pytest.mark.asyncio
async def test_task_output_is_not_sent_and_prompt_is_marked():
    task = {"id": "t1", "chat_id": 1, "prompt": "check the gate",
            "schedule_type": "once", "schedule_value": "x"}
    send_fn = AsyncMock()
    run = AsyncMock(return_value="internal thinking")
    with patch.object(scheduler, "run_in_container", run), \
         patch.object(scheduler, "log_run"), \
         patch.object(scheduler, "update_after_run"), \
         patch.object(scheduler, "_next_run", return_value=None):
        await scheduler._run_task(task, send_fn)
    send_fn.assert_not_called()
    sent_prompt = run.call_args.args[0]
    assert "jarvis-send" in sent_prompt and "check the gate" in sent_prompt
