"""Per-chat async queue — serialises container invocations per chat."""

import asyncio
import logging
from collections import defaultdict
from typing import Callable, Awaitable

log = logging.getLogger(__name__)

RunnerFn = Callable[[str, int], Awaitable[str]]


class ChatQueue:
    def __init__(self, runner: RunnerFn):
        self._runner = runner
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def submit(self, prompt: str, chat_id: int) -> str:
        async with self._locks[chat_id]:
            return await self._runner(prompt, chat_id)
