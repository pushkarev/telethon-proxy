from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from telethon import types


@dataclass(slots=True)
class VirtualUpdateState:
    pts: int = 1
    qts: int = 0
    seq: int = 0
    unread_count: int = 0
    date: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def snapshot(self) -> types.updates.State:
        return types.updates.State(
            pts=self.pts,
            qts=self.qts,
            date=self.date,
            seq=self.seq,
            unread_count=self.unread_count,
        )

    def advance_for_message(self) -> types.updates.State:
        self.pts += 1
        self.seq += 1
        self.date = datetime.now(timezone.utc)
        return self.snapshot()
