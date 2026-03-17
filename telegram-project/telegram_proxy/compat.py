from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from telethon import utils

from .session_state import VirtualUpdateState
from .upstream import UpstreamAdapter


@dataclass(slots=True)
class DownstreamSession:
    session_id: str
    state: VirtualUpdateState


class CompatDispatcher:
    def __init__(self, upstream: UpstreamAdapter) -> None:
        self.upstream = upstream

    async def dispatch(self, session: DownstreamSession, request: dict[str, Any]) -> dict[str, Any]:
        method = request.get("method")
        if method == "get_state":
            return {"ok": True, "state": self._serialize_state(session.state.snapshot())}
        if method == "refresh_policy":
            policy = await self.upstream.refresh_policy()
            return {"ok": True, "allowed_peers": sorted(policy.allowed_peers)}
        if method == "get_dialogs":
            dialogs = await self.upstream.get_dialogs(limit=int(request.get("limit", 100)))
            return {
                "ok": True,
                "dialogs": [self._serialize_dialog(dialog) for dialog in dialogs],
                "state": self._serialize_state(session.state.snapshot()),
            }
        if method == "get_history":
            result = await self.upstream.get_history(request["peer"], limit=int(request.get("limit", 100)))
            return {
                "ok": True,
                "messages": [self._serialize_message(message) for message in result.messages],
                "users": [self._serialize_user(user) for user in result.users],
                "chats": [self._serialize_chat(chat) for chat in result.chats],
                "dropped_count": result.dropped_count,
                "state": self._serialize_state(session.state.snapshot()),
            }
        if method == "send_message":
            message = await self.upstream.send_message(request["peer"], request["message"])
            state = session.state.advance_for_message()
            return {"ok": True, "message": self._serialize_message(message), "state": self._serialize_state(state)}
        if method == "mark_read":
            await self.upstream.mark_read(request["peer"])
            return {"ok": True, "state": self._serialize_state(session.state.snapshot())}
        if method == "list_participants":
            participants = await self.upstream.list_participants(request["peer"], limit=int(request.get("limit", 100)))
            return {"ok": True, "participants": [self._serialize_user(user) for user in participants]}
        return {"ok": False, "error": f"unsupported method: {method}"}

    def _serialize_state(self, state) -> dict[str, Any]:
        return {
            "pts": state.pts,
            "qts": state.qts,
            "seq": state.seq,
            "unread_count": state.unread_count,
            "date": state.date.isoformat(),
        }

    def _serialize_dialog(self, dialog: Any) -> dict[str, Any]:
        return {
            "peer_id": utils.get_peer_id(dialog.entity),
            "name": dialog.name,
            "unread_count": getattr(dialog, "unread_count", 0),
            "is_user": bool(getattr(dialog, "is_user", False)),
            "is_group": bool(getattr(dialog, "is_group", False)),
            "is_channel": bool(getattr(dialog, "is_channel", False)),
        }

    def _serialize_message(self, message: Any) -> dict[str, Any]:
        peer_id = utils.get_peer_id(message.peer_id) if getattr(message, "peer_id", None) else None
        sender = getattr(message, "from_id", None)
        sender_id = utils.get_peer_id(sender) if sender is not None else None
        return {
            "id": message.id,
            "peer_id": peer_id,
            "sender_id": sender_id,
            "text": getattr(message, "message", None),
            "date": message.date.isoformat() if getattr(message, "date", None) else None,
        }

    def _serialize_user(self, user: Any) -> dict[str, Any]:
        return {
            "id": user.id,
            "username": getattr(user, "username", None),
            "first_name": getattr(user, "first_name", None),
            "last_name": getattr(user, "last_name", None),
            "bot": bool(getattr(user, "bot", False)),
        }

    def _serialize_chat(self, chat: Any) -> dict[str, Any]:
        return {
            "id": getattr(chat, "id", None),
            "title": getattr(chat, "title", None),
            "username": getattr(chat, "username", None),
        }
