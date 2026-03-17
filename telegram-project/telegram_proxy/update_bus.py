from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class UpdateEnvelope:
    kind: str
    payload: object
    peer_id: int | None = None
    message_id: int | None = None
    mentioned: bool = False
    incoming: bool = False
    date: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self, serializer) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "peer_id": self.peer_id,
            "message_id": self.message_id,
            "mentioned": self.mentioned,
            "incoming": self.incoming,
            "date": self.date.isoformat(),
            "message": serializer(self.payload) if self.payload is not None else None,
        }


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

    async def publish(self, envelope: UpdateEnvelope) -> None:
        dead: list[asyncio.Queue[UpdateEnvelope]] = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(envelope)
            except asyncio.QueueFull:
                dead.append(queue)
        for queue in dead:
            self._subscribers.discard(queue)
