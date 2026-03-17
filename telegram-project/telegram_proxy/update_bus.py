from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(slots=True)
class UpdateEnvelope:
    payload: object


class UpdateBus:
    def __init__(self, buffer_size: int = 1000) -> None:
        self._buffer_size = buffer_size
        self._subscribers: set[asyncio.Queue[UpdateEnvelope]] = set()

    def subscribe(self) -> asyncio.Queue[UpdateEnvelope]:
        queue: asyncio.Queue[UpdateEnvelope] = asyncio.Queue(maxsize=self._buffer_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[UpdateEnvelope]) -> None:
        self._subscribers.discard(queue)

    async def publish(self, payload: object) -> None:
        dead: list[asyncio.Queue[UpdateEnvelope]] = []
        envelope = UpdateEnvelope(payload=payload)
        for queue in self._subscribers:
            try:
                queue.put_nowait(envelope)
            except asyncio.QueueFull:
                dead.append(queue)
        for queue in dead:
            self._subscribers.discard(queue)
