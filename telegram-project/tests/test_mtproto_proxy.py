import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from telethon import TelegramClient, errors, events, functions, types, utils
from telethon.sessions import StringSession

from telegram_proxy.config import ProxyConfig
from telegram_proxy.downstream_auth import DownstreamAuthService
from telegram_proxy.downstream_registry import DownstreamRegistry
from telegram_proxy.mtproto_service import MTProtoProxyServer
from telegram_proxy.update_bus import UpdateBus, UpdateEnvelope


def _now():
    return datetime(2026, 3, 22, tzinfo=timezone.utc)


def _channel(channel_id: int, *, title: str, username: str, access_hash: int) -> types.Channel:
    return types.Channel(
        id=channel_id,
        title=title,
        username=username,
        access_hash=access_hash,
        megagroup=True,
        photo=types.ChatPhotoEmpty(),
        date=_now(),
    )


def _user(user_id: int, *, first_name: str, access_hash: int) -> types.User:
    return types.User(
        id=user_id,
        first_name=first_name,
        access_hash=access_hash,
    )


def _dialog(peer: types.PeerChannel, top_message: int) -> types.Dialog:
    return types.Dialog(
        peer=peer,
        top_message=top_message,
        read_inbox_max_id=top_message,
        read_outbox_max_id=top_message,
        unread_count=0,
        unread_mentions_count=0,
        unread_reactions_count=0,
        notify_settings=types.PeerNotifySettings(),
    )


def _message(message_id: int, *, peer: types.PeerChannel, sender: types.PeerUser, text: str, out: bool = False) -> types.Message:
    return types.Message(
        id=message_id,
        peer_id=peer,
        from_id=sender,
        message=text,
        out=out,
        date=_now(),
    )


def _document_media(file_name: str, size: int) -> types.MessageMediaDocument:
    return types.MessageMediaDocument(
        document=types.Document(
            id=8000 + size,
            access_hash=9000 + size,
            file_reference=b"",
            date=_now(),
            mime_type="text/plain",
            size=size,
            dc_id=2,
            attributes=[types.DocumentAttributeFilename(file_name=file_name)],
            thumbs=[],
            video_thumbs=[],
        )
    )


class _FakeClientFacade:
    def __init__(self, allowed_channel, sender):
        self.allowed_channel = allowed_channel
        self.sender = sender

    async def get_entity(self, peer):
        if isinstance(peer, types.PeerUser):
            return self.sender
        return self.allowed_channel

    async def get_input_entity(self, _peer):
        return types.InputPeerChannel(self.allowed_channel.id, self.allowed_channel.access_hash)


class FakeUpstream:
    def __init__(self):
        self.allowed_channel = _channel(42, title="Cloud Chat", username="cloudroom", access_hash=4242)
        self.hidden_channel = _channel(99, title="Secret Chat", username="secretroom", access_hash=9900)
        self.sender = _user(1000, first_name="Alice", access_hash=5000)
        self.update_bus = UpdateBus()
        self.messages = [
            _message(7, peer=types.PeerChannel(42), sender=types.PeerUser(1000), text="hello from cloud"),
        ]
        self.client = _FakeClientFacade(self.allowed_channel, self.sender)

    async def iter_allowed_dialogs(self, **_kwargs):
        yield SimpleNamespace(
            dialog=_dialog(types.PeerChannel(42), top_message=self.messages[-1].id),
            message=self.messages[-1],
            entity=self.allowed_channel,
            input_entity=types.InputPeerChannel(42, 4242),
        )

    async def get_history(self, peer, limit=100):
        self._ensure_allowed(peer)
        return SimpleNamespace(
            messages=list(reversed(self.messages[-limit:])),
            chats=[self.allowed_channel],
            users=[self.sender],
            dropped_count=0,
        )

    async def send_message(self, peer, message: str):
        self._ensure_allowed(peer)
        sent = _message(
            self.messages[-1].id + 1,
            peer=types.PeerChannel(42),
            sender=types.PeerUser(1),
            text=message,
            out=True,
        )
        self.messages.append(sent)
        return sent

    async def send_file(
        self,
        peer,
        *,
        data: bytes,
        file_name: str,
        caption: str = "",
        force_document: bool = False,
        mime_type: str | None = None,
        attributes=None,
        formatting_entities=None,
        thumb=None,
        reply_to=None,
    ):
        self._ensure_allowed(peer)
        sent = _message(
            self.messages[-1].id + 1,
            peer=types.PeerChannel(42),
            sender=types.PeerUser(1),
            text=caption,
            out=True,
        )
        sent.media = _document_media(file_name, len(data))
        self.messages.append(sent)
        return sent

    async def read_history(self, peer, max_id: int):
        self._ensure_allowed(peer)
        return None

    async def delete_messages(self, peer, message_ids, *, revoke=True):
        self._ensure_allowed(peer)
        before = len(self.messages)
        self.messages = [message for message in self.messages if message.id not in set(message_ids)]
        deleted = before - len(self.messages)
        return types.messages.AffectedMessages(pts=deleted or 1, pts_count=deleted or 1)

    async def list_participants(self, peer, limit=100):
        self._ensure_allowed(peer)
        return [self.sender][:limit]

    async def resolve_username(self, username: str):
        if username == "cloudroom":
            return types.contacts.ResolvedPeer(
                peer=types.PeerChannel(42),
                chats=[self.allowed_channel],
                users=[],
            )
        raise PermissionError("hidden")

    def _ensure_allowed(self, peer):
        peer_id = utils.get_peer_id(peer)
        if peer_id != utils.get_peer_id(types.PeerChannel(42)):
            raise PermissionError("blocked")


class MTProtoProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        registry_path = Path(self.tmp.name) / "downstream_registry.json"
        self.config = ProxyConfig(
            mtproto_host="127.0.0.1",
            mtproto_port=0,
            downstream_api_id=12345,
            downstream_api_hash="proxyhash",
            downstream_login_code="24680",
            downstream_registry_name=str(registry_path),
        )
        self.registry = DownstreamRegistry(self.config.downstream_registry_path)
        self.server = MTProtoProxyServer(
            self.config,
            FakeUpstream(),
            DownstreamAuthService(self.config),
            self.registry,
        )
        await self.server.start()
        issued = self.registry.issue_session(
            label="test-client",
            host=self.config.mtproto_host,
            port=self.config.mtproto_port,
        )
        self.client = TelegramClient(
            StringSession(issued.session_string),
            self.config.downstream_api_id,
            self.config.downstream_api_hash,
            receive_updates=False,
        )

    async def asyncTearDown(self):
        await self.client.disconnect()
        await self.server.stop()
        self.tmp.cleanup()

    async def _login(self):
        await self.client.start(phone="+15550000000", code_callback=lambda: self.config.downstream_login_code)

    async def test_start_lists_only_cloud_dialogs(self):
        await self._login()

        me = await self.client.get_me()
        self.assertEqual(me.phone, "15550000000")

        dialogs = await self.client.get_dialogs()
        self.assertEqual([dialog.title for dialog in dialogs], ["Cloud Chat"])

    async def test_hidden_chat_cannot_be_resolved_or_read(self):
        await self._login()

        with self.assertRaises(errors.UsernameNotOccupiedError):
            await self.client(functions.contacts.ResolveUsernameRequest("secretroom"))

        with self.assertRaises(errors.PeerIdInvalidError):
            await self.client(
                functions.messages.GetHistoryRequest(
                    peer=types.InputPeerChannel(99, 9900),
                    offset_id=0,
                    offset_date=None,
                    add_offset=0,
                    limit=10,
                    max_id=0,
                    min_id=0,
                    hash=0,
                )
            )

    async def test_send_message_through_proxy(self):
        await self._login()

        dialogs = await self.client.get_dialogs()
        sent = await self.client.send_message(dialogs[0].entity, "proxy says hi")
        self.assertEqual(sent.message, "proxy says hi")

        history = await self.client.get_messages(dialogs[0].entity, limit=2)
        self.assertEqual(history[0].message, "proxy says hi")

    async def test_send_file_through_proxy(self):
        await self._login()

        dialogs = await self.client.get_dialogs()
        sent = await self.client.send_file(dialogs[0].entity, b"hello file", caption="proxy file", force_document=True)

        self.assertEqual(sent.message, "proxy file")
        self.assertIsNotNone(sent.media)

    async def test_delete_message_through_proxy(self):
        await self._login()

        dialogs = await self.client.get_dialogs()
        sent = await self.client.send_message(dialogs[0].entity, "delete me")

        affected = await self.client(functions.messages.DeleteMessagesRequest([sent.id], revoke=True))
        self.assertGreaterEqual(affected.pts_count, 1)

        history = await self.client.get_messages(dialogs[0].entity, limit=5)
        self.assertNotIn("delete me", [message.message for message in history])

    async def test_live_updates_are_delivered_to_telethon_handlers(self):
        seen = []
        event = asyncio.Event()

        @self.client.on(events.NewMessage)
        async def _on_message(update):
            seen.append(update.raw_text)
            event.set()

        await self._login()
        dialogs = await self.client.get_dialogs()
        await self.client.get_messages(dialogs[0].entity, limit=1)

        pushed = _message(
            99,
            peer=types.PeerChannel(42),
            sender=types.PeerUser(1000),
            text="pushed from upstream",
            out=False,
        )
        await self.server.upstream.update_bus.publish(
            UpdateEnvelope(kind="new_message", payload=pushed, peer_id=utils.get_peer_id(pushed.peer_id), message_id=pushed.id)
        )

        await asyncio.wait_for(event.wait(), timeout=2)
        self.assertEqual(seen, ["pushed from upstream"])


if __name__ == "__main__":
    unittest.main()
