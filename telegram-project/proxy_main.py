from __future__ import annotations

import asyncio
import logging

from config_paths import load_project_env
from telegram_proxy.config import ProxyConfig
from telegram_proxy.server import ProxyServer


async def amain() -> None:
    load_project_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = ProxyConfig.from_env()
    server = ProxyServer(config)
    await server.start()
    await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(amain())
