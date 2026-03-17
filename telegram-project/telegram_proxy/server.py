from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import suppress

from .compat import CompatDispatcher, DownstreamSession
from .config import ProxyConfig
from .downstream_auth import DownstreamAuthService
from .session_state import VirtualUpdateState
from .upstream import UpstreamAdapter

logger = logging.getLogger(__name__)


class ProxyServer:
    def __init__(self, config: ProxyConfig) -> None:
        self.config = config
        self.upstream = UpstreamAdapter(config)
        self.auth = DownstreamAuthService(config)
        self.dispatcher = CompatDispatcher(self.upstream, self.auth)
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
        session = DownstreamSession(session_id=str(uuid.uuid4()), state=VirtualUpdateState())
        update_queue = self.upstream.update_bus.subscribe()
        update_task = asyncio.create_task(self._push_updates(writer, update_queue, session))
        try:
            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break
                try:
                    request = json.loads(line.decode("utf-8"))
                    response = await self.dispatcher.dispatch(session, request)
                except Exception as exc:  # deliberate boundary catch for harness stability
                    logger.exception("Request failed")
                    response = {"ok": False, "error": str(exc), "error_type": exc.__class__.__name__}
                writer.write(json.dumps(response).encode("utf-8") + b"\n")
                await writer.drain()
        finally:
            update_task.cancel()
            with suppress(asyncio.CancelledError):
                await update_task
            self.upstream.update_bus.unsubscribe(update_queue)
            writer.close()
            await writer.wait_closed()

    async def _push_updates(self, writer: asyncio.StreamWriter, queue: asyncio.Queue, session: DownstreamSession) -> None:
        while True:
            envelope = await queue.get()
            if session.principal is None:
                continue
            state = session.state.advance_for_message()
            writer.write(
                json.dumps(
                    {
                        "update": envelope.to_dict(self.dispatcher._serialize_message),
                        "state": self.dispatcher._serialize_state(state),
                    }
                ).encode("utf-8")
                + b"\n"
            )
            await writer.drain()
