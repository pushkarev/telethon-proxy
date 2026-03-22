from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from telethon import types, utils


@dataclass(slots=True)
class PendingUpload:
    file_id: int
    total_parts: int | None = None
    big: bool = False
    parts: dict[int, bytes] = field(default_factory=dict)

    def store_part(self, part_index: int, data: bytes, *, total_parts: int | None = None) -> None:
        self.parts[part_index] = data
        if total_parts is not None:
            self.total_parts = total_parts

    def is_complete(self) -> bool:
        if self.total_parts is None:
            return False
        return len(self.parts) >= self.total_parts and all(index in self.parts for index in range(self.total_parts))

    def assemble(self) -> bytes:
        if not self.is_complete():
            raise ValueError("Upload is incomplete")
        return b"".join(self.parts[index] for index in range(self.total_parts or 0))


@dataclass(slots=True)
class VirtualUpdateState:
    pts: int = 1
    qts: int = 0
    seq: int = 0
    unread_count: int = 0
    date: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    known_message_peers: dict[int, int] = field(default_factory=dict)
    pending_uploads: dict[int, PendingUpload] = field(default_factory=dict)

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

    def remember_message(self, message: object) -> None:
        message_id = getattr(message, "id", None)
        peer = getattr(message, "peer_id", None)
        if message_id is None or peer is None:
            return
        self.known_message_peers[message_id] = utils.get_peer_id(peer)

    def remember_messages(self, messages: list[object]) -> None:
        for message in messages:
            self.remember_message(message)

    def track_upload(self, file_id: int, *, big: bool) -> PendingUpload:
        upload = self.pending_uploads.get(file_id)
        if upload is None:
            upload = PendingUpload(file_id=file_id, big=big)
            self.pending_uploads[file_id] = upload
        return upload

    def take_upload(self, file_id: int) -> PendingUpload:
        upload = self.pending_uploads.pop(file_id, None)
        if upload is None:
            raise KeyError(file_id)
        return upload
