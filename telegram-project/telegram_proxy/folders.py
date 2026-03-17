from __future__ import annotations

from telethon import functions, types

from .policy import CloudPolicySnapshot, peer_key, title_text


def _dialog_matches_filter(dialog, dialog_filter, included_keys: set[int], excluded_keys: set[int]) -> bool:
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
    if getattr(dialog_filter, "exclude_muted", False):
        settings = getattr(dialog.dialog, "notify_settings", None)
        if getattr(settings, "mute_until", None) is not None:
            return False
    if getattr(dialog_filter, "exclude_read", False) and getattr(dialog, "unread_count", 0) == 0:
        return False
    if getattr(dialog_filter, "exclude_archived", False) and archived:
        return False

    has_positive_rule = any(
        getattr(dialog_filter, field, False)
        for field in ("contacts", "non_contacts", "groups", "broadcasts", "bots")
    )
    return has_positive_rule


async def build_cloud_policy_snapshot(client, folder_name: str) -> CloudPolicySnapshot:
    filters_result = await client(functions.messages.GetDialogFiltersRequest())
    target_filter = None
    included_keys: set[int] = set()
    excluded_keys: set[int] = set()

    for dialog_filter in filters_result.filters:
        if isinstance(dialog_filter, types.DialogFilter) and title_text(dialog_filter.title) == folder_name:
            target_filter = dialog_filter
            included_keys = {peer_key(peer) for peer in getattr(dialog_filter, "include_peers", [])}
            included_keys.update(peer_key(peer) for peer in getattr(dialog_filter, "pinned_peers", []))
            excluded_keys = {peer_key(peer) for peer in getattr(dialog_filter, "exclude_peers", [])}
            break

    if target_filter is None:
        raise RuntimeError(f"Dialog folder '{folder_name}' not found")

    allowed_peers = set(included_keys)
    async for dialog in client.iter_dialogs(ignore_pinned=False, archived=None):
        if _dialog_matches_filter(dialog, target_filter, included_keys, excluded_keys):
            allowed_peers.add(peer_key(dialog.entity))

    return CloudPolicySnapshot(folder_name=folder_name, allowed_peers=allowed_peers)
