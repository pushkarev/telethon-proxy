from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress

from .config import ProxyConfig
from .upstream import UpstreamAdapter

logger = logging.getLogger(__name__)


class ProxyServer:
    """JSON control server placeholder for the future MTProto facade.

    This is intentionally not MTProto yet. It gives us an executable integration
    harness for the policy engine and update fanout while the wire protocol layer
    is built separately.
    """

    def __init__(self, config: ProxyConfig) -> None:
        self.config = config
        self.upstream = UpstreamAdapter(config)
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        await self.upstream.start()
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.config.listen_host,
            port=self.config.listen_port,
        )
        sockets = ", ".join(str(sock.getsockname()) for sock in (self._server.sockets or []))
        logger.info("Proxy control server listening on %s", sockets)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        await self.upstream.stop()

    async def serve_forever(self) -> None:
        if self._server is None:
            raise RuntimeError("Server not started")
        async with self._server:
            await self._server.serve_forever()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        update_queue = self.upstream.update_bus.subscribe()
        update_task = asyncio.create_task(self._push_updates(writer, update_queue))
        try:
            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break
                request = json.loads(line.decode("utf-8"))
                response = await self._dispatch(request)
                writer.write(json.dumps(response).encode("utf-8") + b"\n")
                await writer.drain()
        finally:
            update_task.cancel()
            with suppress(asyncio.CancelledError):
                await update_task
            self.upstream.update_bus.unsubscribe(update_queue)
            writer.close()
            await writer.wait_closed()

    async def _dispatch(self, request: dict) -> dict:
        method = request.get("method")
        if method == "refresh_policy":
            policy = await self.upstream.refresh_policy()
            return {"ok": True, "allowed_peers": sorted(policy.allowed_peers)}
        if method == "get_dialogs":
            dialogs = await self.upstream.get_dialogs(limit=int(request.get("limit", 100)))
            return {
                "ok": True,
                "dialogs": [
                    {"id": getattr(dialog.entity, "id", None), "name": dialog.name}
                    for dialog in dialogs
                ],
            }
        return {"ok": False, "error": f"unsupported method: {method}"}

    async def _push_updates(self, writer: asyncio.StreamWriter, queue: asyncio.Queue) -> None:
        while True:
            envelope = await queue.get()
            writer.write(json.dumps({"update": str(envelope.payload)}).encode("utf-8") + b"\n")
            await writer.drain()
