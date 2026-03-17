from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from telethon import types, utils


PeerId = int


def title_text(title: object) -> str:
    if isinstance(title, types.TextWithEntities):
        return title.text
    return str(title)


def peer_key(peer: object) -> PeerId:
    return utils.get_peer_id(peer)


@dataclass(slots=True)
class CloudPolicySnapshot:
    folder_name: str
    allowed_peers: set[PeerId] = field(default_factory=set)

    def allows_peer(self, peer: object | None) -> bool:
        if peer is None:
            return False
        return peer_key(peer) in self.allowed_peers

    def allows_peer_id(self, peer_id: PeerId | None) -> bool:
        return peer_id is not None and peer_id in self.allowed_peers

    def restrict_peer_ids(self, peer_ids: Iterable[PeerId]) -> list[PeerId]:
        return [peer_id for peer_id in peer_ids if peer_id in self.allowed_peers]
