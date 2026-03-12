import argparse
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv
from telethon import functions, types, utils
from telethon.sync import TelegramClient


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def title_text(title: object) -> str:
    if isinstance(title, types.TextWithEntities):
        return title.text
    return str(title)


def peer_key(peer: object) -> int:
    try:
        return utils.get_peer_id(peer)
    except TypeError:
        if isinstance(peer, types.InputPeerSelf):
            return utils.get_peer_id(types.PeerUser(user_id=0))
        raise


def is_muted(dialog) -> bool:
    settings = getattr(dialog.dialog, "notify_settings", None)
    mute_until = getattr(settings, "mute_until", None)
    return mute_until is not None


def is_read(dialog) -> bool:
    return getattr(dialog, "unread_count", 0) == 0


def in_filter(dialog, dialog_filter, included_keys: set[int], excluded_keys: set[int]) -> bool:
    dialog_key = peer_key(dialog.entity)
    if dialog_key in excluded_keys:
        return False
    if dialog_key in included_keys:
        return True

    entity = dialog.entity
    is_user = dialog.is_user
    is_group = dialog.is_group
    is_broadcast = dialog.is_channel and not dialog.is_group
    is_bot = bool(getattr(entity, "bot", False))
    is_contact = bool(getattr(entity, "contact", False))
    is_non_contact = is_user and not is_contact
    archived = getattr(dialog.dialog, "folder_id", None) == 1

    if getattr(dialog_filter, "contacts", False) and not is_contact:
        return False
    if getattr(dialog_filter, "non_contacts", False) and not is_non_contact:
        return False
    if getattr(dialog_filter, "groups", False) and not is_group:
        return False
    if getattr(dialog_filter, "broadcasts", False) and not is_broadcast:
        return False
    if getattr(dialog_filter, "bots", False) and not is_bot:
        return False
    if getattr(dialog_filter, "exclude_muted", False) and is_muted(dialog):
        return False
    if getattr(dialog_filter, "exclude_read", False) and is_read(dialog):
        return False
    if getattr(dialog_filter, "exclude_archived", False) and archived:
        return False

    has_positive_rule = any(
        getattr(dialog_filter, field, False)
        for field in ("contacts", "non_contacts", "groups", "broadcasts", "bots")
    )
    return has_positive_rule


def iter_named_filters(filters: Iterable[object]):
    for dialog_filter in filters:
        if isinstance(dialog_filter, types.DialogFilter):
            yield dialog_filter


def build_filter_rules(filters_result):
    named_filters = list(iter_named_filters(filters_result.filters))
    filter_rules = []
    for dialog_filter in named_filters:
        included_keys = {peer_key(peer) for peer in getattr(dialog_filter, "include_peers", [])}
        included_keys.update(peer_key(peer) for peer in getattr(dialog_filter, "pinned_peers", []))
        excluded_keys = {peer_key(peer) for peer in getattr(dialog_filter, "exclude_peers", [])}
        filter_rules.append((title_text(dialog_filter.title), dialog_filter, included_keys, excluded_keys))
    return filter_rules


def dialog_folder_names(dialog, filter_rules) -> list[str]:
    return [
        name
        for name, dialog_filter, included_keys, excluded_keys in filter_rules
        if in_filter(dialog, dialog_filter, included_keys, excluded_keys)
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List your sent messages from chats that are not in any Telegram custom folder."
    )
    parser.add_argument(
        "--months",
        type=float,
        default=1,
        help="Cutoff age in months (approx. 30 days each). Default: 1",
    )
    parser.add_argument(
        "--days",
        type=float,
        help="Cutoff age in days. Overrides --months when provided.",
    )
    parser.add_argument(
        "--limit-per-chat",
        type=int,
        default=20,
        help="Maximum matching messages to print per chat. Default: 20",
    )
    parser.add_argument(
        "--max-chats",
        type=int,
        default=0,
        help="Optional cap on number of unfiled chats to scan. 0 means all.",
    )
    return parser.parse_args()


def cutoff_from_args(args: argparse.Namespace) -> datetime:
    age = timedelta(days=args.days) if args.days is not None else timedelta(days=args.months * 30)
    return datetime.now(UTC) - age


def normalize_message_text(message) -> str:
    text = (message.message or "").replace("\n", " ").strip()
    if text:
        return text
    if message.media:
        return "<media without text>"
    return "<empty>"


def main() -> None:
    args = parse_args()
    load_dotenv()

    api_id = int(require_env("TG_API_ID"))
    api_hash = require_env("TG_API_HASH")
    phone = require_env("TG_PHONE")
    session_name = os.getenv("TG_SESSION_NAME", "sessions/sample_account")

    session_path = Path(session_name)
    session_path.parent.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(str(session_path), api_id, api_hash)

    with client:
        client.start(phone=phone)
        me = client.get_me()
        my_user_id = me.id
        cutoff = cutoff_from_args(args)
        filters_result = client(functions.messages.GetDialogFiltersRequest())
        filter_rules = build_filter_rules(filters_result)

        scanned_unfiled = 0
        for dialog in client.iter_dialogs(ignore_pinned=False, archived=None):
            if dialog_folder_names(dialog, filter_rules):
                continue

            scanned_unfiled += 1
            if args.max_chats and scanned_unfiled > args.max_chats:
                break

            shown = 0
            for message in client.iter_messages(dialog.entity, from_user=my_user_id):
                if message.date is None:
                    continue
                if message.date > cutoff:
                    continue

                print(
                    f"{dialog.name} | {message.date.astimezone(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')} | {normalize_message_text(message)}"
                )
                shown += 1
                if args.limit_per_chat and shown >= args.limit_per_chat:
                    break


if __name__ == "__main__":
    main()
