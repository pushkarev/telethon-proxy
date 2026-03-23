from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


APPLE_EPOCH_OFFSET = 978307200
FIELD_SEPARATOR = "\t"
LIST_SEPARATOR = "\x1f"
DEFAULT_MESSAGES_DB = Path.home() / "Library" / "Messages" / "chat.db"


class IMessageBridgeError(RuntimeError):
    pass


def _truthy(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def _clean_script_value(value: str) -> str:
    text = str(value or "").strip()
    if text.lower() == "missing value":
        return ""
    return text


def _apple_message_date_to_iso(value: object) -> str | None:
    if value in (None, "", 0):
        return None
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return None
    if numeric == 0:
        return None
    if abs(numeric) > 10**12:
        seconds = numeric / 1_000_000_000
    else:
        seconds = float(numeric)
    return datetime.fromtimestamp(seconds + APPLE_EPOCH_OFFSET, tz=timezone.utc).isoformat()


def decode_attributed_body(hex_string: str | None) -> tuple[str | None, str | None]:
    if not hex_string:
        return None, None
    try:
        content = bytes.fromhex(hex_string).decode("utf-8", errors="ignore")
    except ValueError:
        return None, None

    text_patterns = [
        r'NSString">(.*?)<',
        r'NSString">([^<]+)',
        r'NSNumber">\d+<.*?NSString">(.*?)<',
        r'NSArray">.*?NSString">(.*?)<',
        r'"string":\s*"([^"]+)"',
        r'text[^>]*>(.*?)<',
        r'message>(.*?)<',
    ]
    text = None
    for pattern in text_patterns:
        match = re.search(pattern, content, flags=re.DOTALL)
        if match and match.group(1).strip():
            candidate = " ".join(match.group(1).split())
            if len(candidate) > 3:
                text = candidate
                break

    url_patterns = [
        r"(https?://[^\s<\"]+)",
        r'NSString">(https?://[^\s<\"]+)',
        r'"url":\s*"(https?://[^"]+)"',
        r'link[^>]*>(https?://[^<]+)',
    ]
    url = None
    for pattern in url_patterns:
        match = re.search(pattern, content, flags=re.DOTALL)
        if match and match.group(1).strip():
            url = match.group(1).strip()
            break

    metadata_tokens = {
        "streamtyped",
        "nsattributedstring",
        "nsmutableattributedstring",
        "nsdictionary",
        "nsnumber",
        "nsobject",
        "nsmutablestring",
        "nsstring",
        "nsvalue",
    }
    metadata_prefixes = (
        "__kIM",
        "kIM",
        "NSDictionary",
        "NSNumber",
        "NSObject",
        "NSAttributed",
        "NSMutable",
        "NSString",
        "NSValue",
    )
    candidate_chunks: list[tuple[int, str]] = []
    normalized = re.sub(r"[^\w\s\.,!?@:/+\-\(\)\u0400-\u04FF\u2018\u2019\u201C\u201D\U0001F300-\U0001FAFF]+", "\n", content)
    for raw_chunk in normalized.splitlines():
        candidate = " ".join(raw_chunk.split()).strip(" +<>.,!?:;*-_")
        if len(candidate) < 2:
            continue
        lowered = candidate.casefold()
        if lowered in metadata_tokens:
            continue
        if any(lowered.startswith(prefix.casefold()) for prefix in metadata_prefixes):
            continue
        if "__kim" in lowered or "kimmessagepartattribute" in lowered:
            continue
        if not re.search(r"[A-Za-z\u0400-\u04FF]", candidate):
            continue
        candidate = re.sub(r"^[A-Z](?=[A-Z][a-z])", "", candidate)
        score = len(candidate)
        if " " in candidate:
            score += 8
        if re.search(r"[\u0400-\u04FF]", candidate):
            score += 8
        if re.search(r"[.!?]", candidate):
            score += 4
        candidate_chunks.append((score, candidate))

    if candidate_chunks:
        text = max(candidate_chunks, key=lambda item: item[0])[1]

    if not text:
        readable = re.sub(r"[^\x20-\x7E]", " ", content)
        readable = re.sub(r"\s+", " ", readable).strip()
        if len(readable) > 3:
            text = readable

    if text:
        text = re.sub(r"^[+\s]+", "", text)
        text = re.sub(r"\s+", " ", text).strip()
    return text or None, url


ACCOUNT_STATUS_SCRIPT = f"""
on replaceText(subjectText, searchText, replacementText)
  set AppleScript's text item delimiters to searchText
  set textItems to every text item of subjectText
  set AppleScript's text item delimiters to replacementText
  set joinedText to textItems as text
  set AppleScript's text item delimiters to ""
  return joinedText
end replaceText

on sanitizeText(valueText)
  try
    set cleanValue to valueText as text
  on error
    return ""
  end try
  set cleanValue to my replaceText(cleanValue, tab, " ")
  set cleanValue to my replaceText(cleanValue, return, " ")
  set cleanValue to my replaceText(cleanValue, linefeed, " ")
  return cleanValue
end sanitizeText

tell application "Messages"
  set outputLines to {{}}
  repeat with svc in every account
    try
      set end of outputLines to (my sanitizeText(id of svc)) & "{FIELD_SEPARATOR}" & (my sanitizeText(connection status of svc as text)) & "{FIELD_SEPARATOR}" & ((enabled of svc) as text) & "{FIELD_SEPARATOR}" & (my sanitizeText(description of svc)) & "{FIELD_SEPARATOR}" & (my sanitizeText(service type of svc as text))
    end try
  end repeat
  set AppleScript's text item delimiters to linefeed
  set outputText to outputLines as text
  set AppleScript's text item delimiters to ""
  return outputText
end tell
"""


CHAT_LIST_SCRIPT = f"""
on replaceText(subjectText, searchText, replacementText)
  set AppleScript's text item delimiters to searchText
  set textItems to every text item of subjectText
  set AppleScript's text item delimiters to replacementText
  set joinedText to textItems as text
  set AppleScript's text item delimiters to ""
  return joinedText
end replaceText

on sanitizeText(valueText)
  try
    set cleanValue to valueText as text
  on error
    return ""
  end try
  set cleanValue to my replaceText(cleanValue, tab, " ")
  set cleanValue to my replaceText(cleanValue, return, " ")
  set cleanValue to my replaceText(cleanValue, linefeed, " ")
  return cleanValue
end sanitizeText

tell application "Messages"
  set outputLines to {{}}
  set chatIndex to 0
  repeat with c in every chat
    try
      set chatIndex to chatIndex + 1
      set participantHandles to {{}}
      repeat with p in every participant of c
        set end of participantHandles to my sanitizeText(handle of p)
      end repeat
      set AppleScript's text item delimiters to "{LIST_SEPARATOR}"
      set participantText to participantHandles as text
      set AppleScript's text item delimiters to ""
      set chatName to ""
      try
        set chatName to my sanitizeText(name of c)
      end try
      set end of outputLines to (my sanitizeText(id of c)) & "{FIELD_SEPARATOR}" & chatName & "{FIELD_SEPARATOR}" & participantText & "{FIELD_SEPARATOR}" & ((count of participants of c) as text) & "{FIELD_SEPARATOR}" & (my sanitizeText(id of account of c)) & "{FIELD_SEPARATOR}" & (chatIndex as text) & "{FIELD_SEPARATOR}" & (my sanitizeText(service type of (account of c) as text))
    end try
  end repeat
  set AppleScript's text item delimiters to linefeed
  set outputText to outputLines as text
  set AppleScript's text item delimiters to ""
  return outputText
end tell
"""


class IMessageBridge:
    def __init__(
        self,
        *,
        db_path: Path | None = None,
        visible_chats_path: Path | None = None,
    ) -> None:
        self.db_path = Path(db_path or DEFAULT_MESSAGES_DB).expanduser()
        self.visible_chats_path = Path(visible_chats_path or (self.db_path.parent / "visible_chats.json")).expanduser()
        self.visible_chats_path.parent.mkdir(parents=True, exist_ok=True)
        self._visible_chat_ids = self._load_visible_chat_ids()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def close(self) -> None:
        await self.stop()

    async def get_status(self, *, limit: int = 200) -> dict[str, object]:
        return await asyncio.to_thread(self._get_status_sync, limit)

    async def get_chats(self, *, limit: int = 200) -> dict[str, object]:
        return await asyncio.to_thread(self._get_chats_sync, limit)

    async def get_chat(self, chat_id: str, *, limit: int = 50) -> dict[str, object]:
        return await asyncio.to_thread(self._get_chat_sync, chat_id, limit)

    async def get_updates(self, *, limit: int = 50) -> dict[str, object]:
        return await asyncio.to_thread(self._get_updates_sync, limit)

    async def send_message(self, *, chat_id: str, text: str) -> dict[str, object]:
        return await asyncio.to_thread(self._send_message_sync, chat_id, text)

    async def get_local_chat(self, chat_id: str, *, limit: int = 50) -> dict[str, object]:
        return await asyncio.to_thread(self._get_chat_sync, chat_id, limit, False)

    async def set_chat_visibility(self, *, chat_id: str, visible: bool) -> dict[str, object]:
        return await asyncio.to_thread(self._set_chat_visibility_sync, chat_id, visible)

    def _get_status_sync(self, limit: int) -> dict[str, object]:
        accounts, account_error = self._safe_accounts()
        all_chats, chat_error, db_accessible, db_error = self._safe_chat_collection(limit)
        visible_chats = self._filter_visible_chats(all_chats)
        connected = any(str(account.get("connection", "")).lower() == "connected" for account in accounts)
        last_error = account_error or db_error or chat_error
        return {
            "ok": True,
            "available": bool(accounts or all_chats or not last_error),
            "connected": connected,
            "has_session": bool(accounts),
            "messages_app_accessible": account_error is None or chat_error is None,
            "database_accessible": db_accessible,
            "messages_app_error": account_error or chat_error,
            "database_error": db_error,
            "automation_hint": "Grant Automation access to Messages and Full Disk Access for history reads if macOS prompts.",
            "db_path": str(self.db_path),
            "accounts": accounts,
            "all_chats": all_chats,
            "visible_chats": visible_chats,
            "visible_chat_ids": [chat["chat_id"] for chat in visible_chats],
            "chats": visible_chats,
            "last_error": last_error,
        }

    def _get_chats_sync(self, limit: int) -> dict[str, object]:
        all_chats, chat_error, db_accessible, db_error = self._safe_chat_collection(limit)
        visible_chats = self._filter_visible_chats(all_chats)
        if chat_error and not all_chats:
            raise IMessageBridgeError(chat_error)
        return {
            "ok": True,
            "chats": visible_chats,
            "database_accessible": db_accessible,
            "database_error": db_error,
        }

    def _get_chat_sync(self, chat_id: str, limit: int, visible_only: bool = True) -> dict[str, object]:
        chat_id = str(chat_id or "").strip()
        if not chat_id:
            raise IMessageBridgeError("iMessage chat_id is required")
        chats, _chat_error, _db_accessible, _db_error = self._safe_chat_collection(1000)
        if visible_only:
            chats = self._filter_visible_chats(chats)
        chat = next((item for item in chats if item.get("chat_id") == chat_id), None)
        if chat is None:
            if visible_only:
                raise IMessageBridgeError(f"iMessage chat is not visible downstream: {chat_id}")
            raise IMessageBridgeError(f"Unknown iMessage chat: {chat_id}")
        try:
            messages = self._query_chat_messages(chat_id, limit)
        except IMessageBridgeError as exc:
            raise IMessageBridgeError(str(exc)) from exc
        return {"ok": True, "chat": chat, "messages": messages}

    def _get_updates_sync(self, limit: int) -> dict[str, object]:
        messages = [message for message in self._query_recent_messages(limit=max(limit * 4, 200)) if self._is_visible_chat_id(message.get("chat_id"))]
        messages = messages[-limit:]
        updates = [
            {
                "kind": "new_message",
                "chat_id": message["chat_id"],
                "message_id": message["id"],
                "message": message,
            }
            for message in messages
        ]
        return {"ok": True, "updates": updates}

    def _send_message_sync(self, chat_id: str, text: str) -> dict[str, object]:
        chat_id = str(chat_id or "").strip()
        message_text = str(text or "").strip()
        if not chat_id:
            raise IMessageBridgeError("iMessage chat_id is required")
        if not message_text:
            raise IMessageBridgeError("Message text is required")
        if not self._is_visible_chat_id(chat_id):
            raise IMessageBridgeError(f"iMessage chat is not visible downstream: {chat_id}")
        script = f"""
tell application "Messages"
  repeat with c in every chat
    try
      if (id of c as text) is {json.dumps(chat_id)} then
        send {json.dumps(message_text)} to c
        return (id of c as text)
      end if
    end try
  end repeat
end tell
error "iMessage chat not found"
"""
        self._run_script(script)
        message = {
            "id": None,
            "chat_id": chat_id,
            "sender": None,
            "text": message_text,
            "date": datetime.now(timezone.utc).isoformat(),
            "from_me": True,
            "kind": "text",
        }
        return {"ok": True, "message": message}

    def _set_chat_visibility_sync(self, chat_id: str, visible: bool) -> dict[str, object]:
        normalized_chat_id = str(chat_id or "").strip()
        if not normalized_chat_id:
            raise IMessageBridgeError("iMessage chat_id is required")
        chats, chat_error, _db_accessible, _db_error = self._safe_chat_collection(5000)
        if not any(chat.get("chat_id") == normalized_chat_id for chat in chats):
            if chat_error and not chats:
                raise IMessageBridgeError(chat_error)
            raise IMessageBridgeError(f"Unknown iMessage chat: {normalized_chat_id}")
        updated = set(self._visible_chat_ids)
        if visible:
            updated.add(normalized_chat_id)
        else:
            updated.discard(normalized_chat_id)
        self._visible_chat_ids = updated
        self._save_visible_chat_ids()
        visible_chats = self._filter_visible_chats(chats)
        return {
            "ok": True,
            "chat_id": normalized_chat_id,
            "visible": visible,
            "visible_chat_ids": [chat["chat_id"] for chat in visible_chats],
            "visible_chats": visible_chats,
            "all_chats": chats,
            "chats": visible_chats,
        }

    def _safe_accounts(self) -> tuple[list[dict[str, object]], str | None]:
        try:
            return self._list_accounts(), None
        except IMessageBridgeError as exc:
            return [], str(exc)

    def _safe_chat_collection(self, limit: int) -> tuple[list[dict[str, object]], str | None, bool, str | None]:
        script_chats: dict[str, dict[str, object]] = {}
        chat_error = None
        try:
            script_chats = {chat["chat_id"]: chat for chat in self._list_scriptable_chats()}
        except IMessageBridgeError as exc:
            chat_error = str(exc)

        db_summaries: dict[str, dict[str, object]] = {}
        db_error = None
        db_accessible = True
        try:
            db_summaries = {chat["chat_id"]: chat for chat in self._query_chat_summaries(limit=max(limit, 500))}
        except IMessageBridgeError as exc:
            db_accessible = False
            db_error = str(exc)

        merged = self._merge_chats(script_chats, db_summaries)
        return merged[:limit], chat_error, db_accessible, db_error

    def _filter_visible_chats(self, chats: list[dict[str, object]]) -> list[dict[str, object]]:
        return [chat for chat in chats if self._is_visible_chat_id(chat.get("chat_id"))]

    def _is_visible_chat_id(self, chat_id: object) -> bool:
        normalized = str(chat_id or "").strip()
        return bool(normalized) and normalized in self._visible_chat_ids

    def _load_visible_chat_ids(self) -> set[str]:
        if not self.visible_chats_path.exists():
            return set()
        try:
            payload = json.loads(self.visible_chats_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        if not isinstance(payload, dict):
            return set()
        chat_ids = payload.get("chat_ids")
        if not isinstance(chat_ids, list):
            return set()
        return {str(chat_id).strip() for chat_id in chat_ids if str(chat_id).strip()}

    def _save_visible_chat_ids(self) -> None:
        payload = {
            "chat_ids": sorted(self._visible_chat_ids),
        }
        tmp_path = self.visible_chats_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp_path.replace(self.visible_chats_path)

    def _merge_chats(
        self,
        script_chats: dict[str, dict[str, object]],
        db_summaries: dict[str, dict[str, object]],
    ) -> list[dict[str, object]]:
        merged: list[dict[str, object]] = []
        all_chat_ids = set(script_chats) | set(db_summaries)
        for chat_id in all_chat_ids:
            script_chat = script_chats.get(chat_id, {})
            db_chat = db_summaries.get(chat_id, {})
            participants = script_chat.get("participants") or db_chat.get("participants") or []
            title = (
                script_chat.get("title")
                or db_chat.get("title")
                or (participants[0] if len(participants) == 1 else ", ".join(participants[:3]))
                or chat_id
            )
            participant_count = int(script_chat.get("participant_count") or db_chat.get("participant_count") or len(participants) or 0)
            kind = db_chat.get("kind") or ("group" if participant_count > 1 else "dm")
            merged.append(
                {
                    "chat_id": chat_id,
                    "title": title,
                    "kind": kind,
                    "service_type": script_chat.get("service_type") or db_chat.get("service_type") or "Messages",
                    "participants": participants,
                    "participant_count": participant_count,
                    "last_message_at": db_chat.get("last_message_at"),
                    "last_message_text": db_chat.get("last_message_text"),
                    "unread_count": int(db_chat.get("unread_count") or 0),
                    "account_id": script_chat.get("account_id") or db_chat.get("account_id"),
                    "script_order": script_chat.get("script_order"),
                }
            )
        merged.sort(key=lambda chat: (chat.get("title") or "").casefold())
        merged.sort(key=lambda chat: chat.get("script_order") if chat.get("script_order") is not None else 10**9)
        merged.sort(key=lambda chat: chat.get("last_message_at") or "", reverse=True)
        return merged

    def _list_accounts(self) -> list[dict[str, object]]:
        rows = self._run_script(ACCOUNT_STATUS_SCRIPT)
        accounts: list[dict[str, object]] = []
        for line in rows.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(FIELD_SEPARATOR)
            while len(parts) < 5:
                parts.append("")
            account_id, connection, enabled, description, service_type = parts[:5]
            accounts.append(
                {
                    "id": _clean_script_value(account_id),
                    "connection": _clean_script_value(connection),
                    "enabled": _truthy(enabled),
                    "description": _clean_script_value(description),
                    "service_type": _clean_script_value(service_type) or "Messages",
                }
            )
        return accounts

    def _list_scriptable_chats(self) -> list[dict[str, object]]:
        rows = self._run_script(CHAT_LIST_SCRIPT)
        chats: list[dict[str, object]] = []
        for line in rows.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(FIELD_SEPARATOR)
            while len(parts) < 7:
                parts.append("")
            chat_id, title, participants_text, participant_count, account_id, script_order, service_type = parts[:7]
            participants = [_clean_script_value(value) for value in participants_text.split(LIST_SEPARATOR) if _clean_script_value(value)]
            chats.append(
                {
                    "chat_id": _clean_script_value(chat_id),
                    "title": _clean_script_value(title),
                    "participants": participants,
                    "participant_count": int(participant_count or len(participants) or 0),
                    "kind": "group" if int(participant_count or len(participants) or 0) > 1 else "dm",
                    "account_id": _clean_script_value(account_id) or None,
                    "script_order": int(script_order or 0) or None,
                    "service_type": _clean_script_value(service_type) or "Messages",
                }
            )
        return chats

    def _query_chat_summaries(self, *, limit: int) -> list[dict[str, object]]:
        query = """
            SELECT
                c.guid AS chat_id,
                COALESCE(NULLIF(c.display_name, ''), NULLIF(c.chat_identifier, ''), NULLIF(c.room_name, ''), c.guid) AS title,
                COUNT(DISTINCT chj.handle_id) AS participant_count,
                GROUP_CONCAT(DISTINCT COALESCE(h.id, h.uncanonicalized_id)) AS participants,
                COALESCE(NULLIF(c.service_name, ''), 'Messages') AS service_type,
                MAX(m.date) AS last_message_date,
                (
                    SELECT COALESCE(NULLIF(m2.text, ''), NULLIF(hex(m2.attributedBody), ''))
                    FROM message m2
                    JOIN chat_message_join cmj2 ON cmj2.message_id = m2.ROWID
                    WHERE cmj2.chat_id = c.ROWID
                    ORDER BY m2.date DESC
                    LIMIT 1
                ) AS last_message_text,
                COALESCE(SUM(CASE WHEN m.is_from_me = 0 AND COALESCE(m.is_read, 1) = 0 THEN 1 ELSE 0 END), 0) AS unread_count
            FROM chat c
            LEFT JOIN chat_handle_join chj ON chj.chat_id = c.ROWID
            LEFT JOIN handle h ON h.ROWID = chj.handle_id
            LEFT JOIN chat_message_join cmj ON cmj.chat_id = c.ROWID
            LEFT JOIN message m ON m.ROWID = cmj.message_id
            GROUP BY c.ROWID
            ORDER BY COALESCE(MAX(m.date), 0) DESC
            LIMIT ?
        """
        with closing(self._connect_db()) as conn:
            rows = conn.execute(query, (int(limit),)).fetchall()
        summaries = []
        for row in rows:
            participant_handles = [value for value in (row["participants"] or "").split(",") if value]
            last_text = self._normalize_message_text(row["last_message_text"], None, [])
            summaries.append(
                {
                    "chat_id": row["chat_id"],
                    "title": row["title"],
                    "participants": participant_handles,
                    "participant_count": int(row["participant_count"] or len(participant_handles) or 0),
                    "kind": "group" if int(row["participant_count"] or 0) > 1 else "dm",
                    "service_type": row["service_type"],
                    "last_message_at": _apple_message_date_to_iso(row["last_message_date"]),
                    "last_message_text": last_text,
                    "unread_count": int(row["unread_count"] or 0),
                }
            )
        return summaries

    def _query_chat_messages(self, chat_id: str, limit: int) -> list[dict[str, object]]:
        query = """
            SELECT
                m.ROWID AS message_id,
                c.guid AS chat_id,
                COALESCE(NULLIF(c.service_name, ''), 'Messages') AS service_type,
                COALESCE(h.id, h.uncanonicalized_id) AS sender,
                m.text AS text,
                hex(m.attributedBody) AS attributed_body_hex,
                m.date AS message_date,
                m.is_from_me AS is_from_me,
                m.subject AS subject,
                m.cache_has_attachments AS has_attachments
            FROM message m
            JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
            JOIN chat c ON c.ROWID = cmj.chat_id
            LEFT JOIN handle h ON h.ROWID = m.handle_id
            WHERE c.guid = ?
            ORDER BY m.date DESC
            LIMIT ?
        """
        with closing(self._connect_db()) as conn:
            rows = conn.execute(query, (chat_id, int(limit))).fetchall()
            messages = [self._serialize_db_message(conn, row) for row in rows]
        messages.reverse()
        return messages

    def _query_recent_messages(self, limit: int) -> list[dict[str, object]]:
        query = """
            SELECT
                m.ROWID AS message_id,
                c.guid AS chat_id,
                COALESCE(NULLIF(c.service_name, ''), 'Messages') AS service_type,
                COALESCE(h.id, h.uncanonicalized_id) AS sender,
                m.text AS text,
                hex(m.attributedBody) AS attributed_body_hex,
                m.date AS message_date,
                m.is_from_me AS is_from_me,
                m.subject AS subject,
                m.cache_has_attachments AS has_attachments
            FROM message m
            JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
            JOIN chat c ON c.ROWID = cmj.chat_id
            LEFT JOIN handle h ON h.ROWID = m.handle_id
            ORDER BY m.date DESC
            LIMIT ?
        """
        with closing(self._connect_db()) as conn:
            rows = conn.execute(query, (int(limit),)).fetchall()
            messages = [self._serialize_db_message(conn, row) for row in rows]
        messages.reverse()
        return messages

    def _serialize_db_message(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
        attachments = self._attachment_paths(conn, int(row["message_id"])) if int(row["has_attachments"] or 0) else []
        text = self._normalize_message_text(row["text"], row["attributed_body_hex"], attachments, row["subject"])
        return {
            "id": str(row["message_id"]),
            "chat_id": row["chat_id"],
            "sender": row["sender"],
            "text": text,
            "date": _apple_message_date_to_iso(row["message_date"]),
            "from_me": bool(row["is_from_me"]),
            "kind": "text",
            "service_type": row["service_type"],
            "attachments": attachments,
        }

    def _attachment_paths(self, conn: sqlite3.Connection, message_id: int) -> list[str]:
        query = """
            SELECT attachment.filename
            FROM attachment
            JOIN message_attachment_join ON attachment.ROWID = message_attachment_join.attachment_id
            WHERE message_attachment_join.message_id = ?
        """
        rows = conn.execute(query, (int(message_id),)).fetchall()
        return [str(row["filename"]) for row in rows if row["filename"]]

    def _normalize_message_text(
        self,
        text: object,
        attributed_body_hex: object,
        attachments: list[str],
        subject: object | None = None,
    ) -> str:
        plain_text = str(text).strip() if text not in (None, "") else ""
        attributed_hex = str(attributed_body_hex).strip() if attributed_body_hex not in (None, "") else ""
        if (
            plain_text
            and not attributed_hex
            and len(plain_text) >= 32
            and len(plain_text) % 2 == 0
            and re.fullmatch(r"[0-9A-Fa-f]+", plain_text)
        ):
            attributed_hex = plain_text
            plain_text = ""
        url = None
        if not plain_text and attributed_hex:
            plain_text, url = decode_attributed_body(attributed_hex)
            plain_text = plain_text or ""
        if not plain_text:
            plain_text = "[No text content]"
        if subject not in (None, ""):
            plain_text = f"Subject: {subject}\n{plain_text}"
        if attachments:
            plain_text += f"\n[Attachments: {len(attachments)}]"
        if url:
            plain_text += f"\n[URL: {url}]"
        return plain_text

    def _connect_db(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise IMessageBridgeError(self._format_db_error(exc)) from exc
        conn.row_factory = sqlite3.Row
        return conn

    def _format_db_error(self, exc: sqlite3.Error) -> str:
        message = str(exc) or exc.__class__.__name__
        if "authorization denied" in message.lower() or "unable to open database file" in message.lower():
            return (
                "Messages history is blocked by macOS privacy. Grant Full Disk Access to the app or terminal "
                "that runs this proxy to read chat history from chat.db."
            )
        return message

    def _run_script(self, script: str) -> str:
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
        except OSError as exc:
            raise IMessageBridgeError(str(exc) or exc.__class__.__name__) from exc
        except subprocess.TimeoutExpired as exc:
            raise IMessageBridgeError("Messages automation timed out") from exc
        if result.returncode != 0:
            error_text = (result.stderr or result.stdout or "").strip()
            raise IMessageBridgeError(error_text or "Messages automation failed")
        return result.stdout.strip()
