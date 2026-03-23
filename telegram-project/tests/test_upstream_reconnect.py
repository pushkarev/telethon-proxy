import asyncio
import unittest
from types import SimpleNamespace

from telethon import types, utils

import telegram_proxy.upstream as upstream_module
from telegram_proxy.config import ProxyConfig
from telegram_proxy.policy import CloudPolicySnapshot
from telegram_proxy.upstream import UpstreamAdapter


class _FakeClient:
    def __init__(self) -> None:
        self.authorized = True
        self.connected = False
        self.connect_failures = 0
        self.connect_calls = 0
        self.handlers = []
        self.entity = types.User(id=42, first_name="Cloud", access_hash=4200)
        self.dialogs = [SimpleNamespace(entity=self.entity)]
        self._disconnected = asyncio.get_running_loop().create_future()

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_failures > 0:
            self.connect_failures -= 1
            raise OSError("network unreachable")
        self.connected = True
        self._disconnected = asyncio.get_running_loop().create_future()

    async def disconnect(self) -> None:
        self.connected = False
        if not self._disconnected.done():
            self._disconnected.set_result(None)

    async def is_user_authorized(self) -> bool:
        return self.authorized

    def is_connected(self) -> bool:
        return self.connected

    @property
    def disconnected(self):
        return self._disconnected

    def add_event_handler(self, callback, event) -> None:
        self.handlers.append((callback, event))

    async def get_dialogs(self, limit=100, **_kwargs):
        if not self.connected:
            raise OSError("network unreachable")
        return self.dialogs[:limit]

    def drop_connection(self) -> None:
        self.connected = False
        if not self._disconnected.done():
            self._disconnected.set_result(None)


class UpstreamReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = _FakeClient()
        self.policy_builds = 0
        self.original_build = upstream_module.build_cloud_policy_snapshot

        async def fake_build(_client, folder_name):
            self.policy_builds += 1
            return CloudPolicySnapshot(
                folder_name=folder_name,
                allowed_peers={utils.get_peer_id(self.client.entity)},
            )

        upstream_module.build_cloud_policy_snapshot = fake_build
        self.config = ProxyConfig(
            upstream_api_id=1,
            upstream_api_hash="hash",
            upstream_reconnect_min_delay=0.01,
            upstream_reconnect_max_delay=0.05,
        )
        self.adapter = UpstreamAdapter(self.config, client=self.client)

    async def asyncTearDown(self) -> None:
        upstream_module.build_cloud_policy_snapshot = self.original_build
        await self.adapter.stop()

    async def test_start_recovers_after_initial_network_failure(self) -> None:
        self.client.connect_failures = 1

        await self.adapter.start()

        await self._wait_for(lambda: self.client.connected and self.policy_builds >= 1)
        dialogs = await self.adapter.get_dialogs()

        self.assertEqual(len(dialogs), 1)
        self.assertEqual(len(self.client.handlers), 2)
        self.assertGreaterEqual(self.client.connect_calls, 2)

    async def test_reconnects_after_live_disconnect(self) -> None:
        await self.adapter.start()
        await self._wait_for(lambda: self.client.connected and self.policy_builds >= 1)

        initial_builds = self.policy_builds
        self.client.drop_connection()

        await self._wait_for(lambda: self.client.connected and self.policy_builds > initial_builds)
        self.assertGreaterEqual(self.client.connect_calls, 2)

    async def test_get_dialogs_applies_limit_after_cloud_filtering(self) -> None:
        allowed_entity_1 = types.User(id=100, first_name="Allowed 1", access_hash=1000)
        allowed_entity_2 = types.User(id=200, first_name="Allowed 2", access_hash=2000)
        blocked_entity = types.User(id=300, first_name="Blocked", access_hash=3000)
        self.client.entity = allowed_entity_1
        self.client.dialogs = [
            SimpleNamespace(entity=blocked_entity),
            SimpleNamespace(entity=allowed_entity_1),
            SimpleNamespace(entity=allowed_entity_2),
        ]

        async def fake_build(_client, folder_name):
            self.policy_builds += 1
            return CloudPolicySnapshot(
                folder_name=folder_name,
                allowed_peers={
                    utils.get_peer_id(allowed_entity_1),
                    utils.get_peer_id(allowed_entity_2),
                },
            )

        upstream_module.build_cloud_policy_snapshot = fake_build

        await self.adapter.start()
        await self._wait_for(lambda: self.client.connected and self.policy_builds >= 1)

        dialogs = await self.adapter.get_dialogs(limit=2)

        self.assertEqual([dialog.entity.id for dialog in dialogs], [100, 200])

    async def _wait_for(self, predicate, timeout: float = 0.5) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail("condition was not met before timeout")
