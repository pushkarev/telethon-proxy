import os
import asyncio
from pathlib import Path
from typing import Iterable

from config_paths import load_project_env
from telethon import functions, types, utils
from telethon import TelegramClient
from telegram_auth import prompt_value, resolve_runtime_credentials


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


async def amain() -> None:
    load_project_env()

    credentials = resolve_runtime_credentials(require_phone=False)
    session_name = os.getenv("TG_SESSION_NAME", str(Path.home() / ".tlt-proxy/sessions/sample_account"))

    session_path = Path(session_name).expanduser()
    session_path.parent.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(str(session_path), credentials.api_id, credentials.api_hash)
    await client.connect()

    try:
        if not await client.is_user_authorized():
            phone = credentials.phone or prompt_value("TG_PHONE", "Telegram phone number: ")[0]
            await client.start(phone=phone)

        filters_result = await client(functions.messages.GetDialogFiltersRequest())
        named_filters = list(iter_named_filters(filters_result.filters))

        filter_rules = []
        for dialog_filter in named_filters:
            included_keys = {peer_key(peer) for peer in getattr(dialog_filter, "include_peers", [])}
            included_keys.update(peer_key(peer) for peer in getattr(dialog_filter, "pinned_peers", []))
            excluded_keys = {peer_key(peer) for peer in getattr(dialog_filter, "exclude_peers", [])}
            filter_rules.append((title_text(dialog_filter.title), dialog_filter, included_keys, excluded_keys))

        async for dialog in client.iter_dialogs(ignore_pinned=False, archived=None):
            folder_names = [
                name
                for name, dialog_filter, included_keys, excluded_keys in filter_rules
                if in_filter(dialog, dialog_filter, included_keys, excluded_keys)
            ]
            suffix = ", ".join(folder_names) if folder_names else "<none>"
            print(f"{dialog.name}: {suffix}")
    finally:
        await client.disconnect()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
