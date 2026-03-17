from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class HookDeliveryResult:
    delivered: bool
    returncode: int | None = None


class IncomingHook:
    def __init__(self, command: str | None) -> None:
        self.command = (command or "").strip()

    async def deliver(self, payload: dict) -> HookDeliveryResult:
        if not self.command:
            return HookDeliveryResult(delivered=False, returncode=None)
        proc = await asyncio.create_subprocess_shell(
            self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        await proc.communicate(body)
        logger.info("Delivered incoming hook via %s with rc=%s", self.command, proc.returncode)
        return HookDeliveryResult(delivered=True, returncode=proc.returncode)
