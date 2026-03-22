from __future__ import annotations

from .config import ProxyConfig
from .downstream_auth import DownstreamAuthService
from .downstream_registry import DownstreamRegistry
from .mtproto_service import MTProtoProxyServer
from .upstream import UpstreamAdapter


class ProxyService:
    def __init__(self, config: ProxyConfig) -> None:
        self.config = config
        self.upstream = UpstreamAdapter(config)
        self.auth = DownstreamAuthService(config)
        self.registry = DownstreamRegistry(config.downstream_registry_path)
        self.mtproto = MTProtoProxyServer(config, self.upstream, self.auth, self.registry)

    async def start(self) -> None:
        await self.upstream.start()
        await self.mtproto.start()

    async def stop(self) -> None:
        await self.mtproto.stop()
        await self.upstream.stop()

    async def serve_forever(self) -> None:
        await self.mtproto.serve_forever()
