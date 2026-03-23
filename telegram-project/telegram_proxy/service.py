from __future__ import annotations

import logging
import asyncio

from .config import ProxyConfig
from .dashboard_service import ProxyDashboardServer
from .downstream_auth import DownstreamAuthService
from .downstream_registry import DownstreamRegistry
from .imessage_bridge import IMessageBridge
from .mcp_service import McpServer
from .mtproto_service import MTProtoProxyServer
from .secrets_store import MacOSSecretStore
from .telegram_auth_service import TelegramAuthService
from .upstream import UpstreamAdapter
from .whatsapp_bridge import WhatsAppBridge, WhatsAppBridgeError


logger = logging.getLogger(__name__)


class ProxyService:
    def __init__(self, config: ProxyConfig) -> None:
        self.config = config
        self._stop_event = asyncio.Event()
        self.secret_store = MacOSSecretStore()
        self.upstream = UpstreamAdapter(config)
        self.whatsapp = WhatsAppBridge(
            host=config.whatsapp_host,
            port=config.whatsapp_port,
            cloud_label_name=config.whatsapp_cloud_label_name,
            auth_dir=config.whatsapp_auth_path,
        )
        self.imessage = IMessageBridge(
            db_path=config.imessage_db_path,
            visible_chats_path=config.imessage_visible_chats_path,
        )
        self.telegram_auth = TelegramAuthService(config, self.upstream, secret_store=self.secret_store)
        self.auth = DownstreamAuthService(config)
        self.registry = DownstreamRegistry(config.downstream_registry_path)
        self.mtproto = MTProtoProxyServer(config, self.upstream, self.auth, self.registry)
        self.mcp = McpServer(config, self.upstream, whatsapp=self.whatsapp, imessage=self.imessage)
        self.dashboard = ProxyDashboardServer(
            config,
            self.upstream,
            self.registry,
            self.mtproto,
            self.mcp,
            self.telegram_auth,
            whatsapp=self.whatsapp,
            imessage=self.imessage,
            secret_store=self.secret_store,
        )

    async def start(self) -> None:
        self._stop_event.clear()
        await self.upstream.start()
        try:
            await self.whatsapp.start()
        except WhatsAppBridgeError as exc:
            logger.warning("WhatsApp bridge failed to start; continuing without an active bridge: %s", exc)
        if self.config.mtproto_enabled:
            await self.mtproto.start()
        await self.mcp.start()
        await self.dashboard.start()

    async def stop(self) -> None:
        self._stop_event.set()
        await self.dashboard.stop()
        await self.mcp.stop()
        await self.mtproto.stop()
        await self.telegram_auth.close()
        await self.imessage.close()
        await self.whatsapp.close()
        await self.upstream.stop()

    async def serve_forever(self) -> None:
        await self._stop_event.wait()
