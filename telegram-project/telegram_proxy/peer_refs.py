from __future__ import annotations

from typing import Any

from telethon import types, utils


class PeerResolver:
    def normalize_peer_ref(self, peer: Any) -> Any:
        if isinstance(peer, (types.TypePeer, types.TypeInputPeer)):
            return peer
        if isinstance(peer, int):
            return utils.resolve_id(peer)[0]
        if isinstance(peer, str):
            text = peer.strip()
            if text == "me":
                return "me"
            if text.lstrip("-").isdigit():
                return utils.resolve_id(int(text))[0]
            return text
        return peer

    def peer_id(self, peer: Any) -> int:
        normalized = self.normalize_peer_ref(peer)
        if isinstance(normalized, int):
            return normalized
        return utils.get_peer_id(normalized)
