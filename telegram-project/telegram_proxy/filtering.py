from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from telethon import types, utils

from .policy import CloudPolicySnapshot


@dataclass(slots=True)
class FilterResult:
    messages: list[object]
    chats: list[object]
    users: list[object]
    dropped_count: int = 0


def _peer_id_from_message(message: object) -> int | None:
    peer_id = getattr(message, "peer_id", None)
    if peer_id is None:
        return None
    return utils.get_peer_id(peer_id)


def _sender_id_from_message(message: object) -> int | None:
    sender_id = getattr(message, "from_id", None)
    if sender_id is None:
        return None
    return utils.get_peer_id(sender_id)


def _collect_referenced_peer_ids(messages: Sequence[object]) -> set[int]:
    peer_ids: set[int] = set()
    for message in messages:
        peer_id = _peer_id_from_message(message)
        if peer_id is not None:
            peer_ids.add(peer_id)
        sender_id = _sender_id_from_message(message)
        if sender_id is not None:
            peer_ids.add(sender_id)
        fwd = getattr(message, "fwd_from", None)
        if fwd and getattr(fwd, "from_id", None) is not None:
            peer_ids.add(utils.get_peer_id(fwd.from_id))
    return peer_ids


def filter_messages_bundle(
    *,
    policy: CloudPolicySnapshot,
    messages: Iterable[object],
    chats: Iterable[object],
    users: Iterable[object],
    allow_member_listing: bool,
) -> FilterResult:
    kept_messages: list[object] = []
    dropped_count = 0

    for message in messages:
        peer_id = _peer_id_from_message(message)
        if policy.allows_peer_id(peer_id):
            fwd = getattr(message, "fwd_from", None)
            if fwd and getattr(fwd, "from_id", None) is not None and not policy.allows_peer_id(utils.get_peer_id(fwd.from_id)):
                dropped_count += 1
                continue
            kept_messages.append(message)
        else:
            dropped_count += 1

    referenced_peer_ids = _collect_referenced_peer_ids(kept_messages)

    kept_chats = [chat for chat in chats if policy.allows_peer_id(utils.get_peer_id(chat))]
    kept_users = []
    for user in users:
        user_peer_id = utils.get_peer_id(types.PeerUser(user.id))
        if user_peer_id in referenced_peer_ids:
            if policy.allows_peer_id(user_peer_id) or allow_member_listing:
                kept_users.append(user)

    return FilterResult(
        messages=kept_messages,
        chats=kept_chats,
        users=kept_users,
        dropped_count=dropped_count,
    )


def ensure_allowed_peer(policy: CloudPolicySnapshot, peer: object, *, action: str) -> None:
    if not policy.allows_peer(peer):
        raise PermissionError(f"Blocked {action} for peer outside Cloud folder")
