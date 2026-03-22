from __future__ import annotations

from typing import Any

from telethon import types, utils


class PeerResolver:
    def normalize_peer_ref(self, peer: Any) -> Any:
        if isinstance(peer, (types.TypePeer, types.TypeInputPeer)):
            return peer
        if isinstance(peer, int):
            return self._peer_from_id(peer)
        if isinstance(peer, str):
            text = peer.strip()
            if text == "me":
                return "me"
            if text.lstrip("-").isdigit():
                return self._peer_from_id(int(text))
            return text
        return peer

    def peer_id(self, peer: Any) -> int:
        normalized = self.normalize_peer_ref(peer)
        if isinstance(normalized, int):
            return normalized
        return utils.get_peer_id(normalized)

    def _peer_from_id(self, peer_id: int) -> Any:
        real_id, peer_type = utils.resolve_id(peer_id)
        return peer_type(real_id)
