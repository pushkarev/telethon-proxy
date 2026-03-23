from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest


class WhatsAppBridgeError(RuntimeError):
    pass


def resolve_node_bin(explicit: str | None = None) -> str:
    candidates = [
        explicit,
        os.getenv("TP_NODE_BIN"),
        shutil.which("node"),
        "/opt/homebrew/bin/node",
        "/usr/local/bin/node",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return explicit or os.getenv("TP_NODE_BIN") or "node"


class WhatsAppBridge:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        cloud_label_name: str,
        auth_dir: Path,
        node_bin: str | None = None,
        service_path: Path | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.cloud_label_name = cloud_label_name
        self.auth_dir = auth_dir
        self.node_bin = resolve_node_bin(node_bin)
        self.service_path = service_path or Path(__file__).resolve().parents[2] / "whatsapp-project" / "service.mjs"
        self.process: subprocess.Popen[str] | None = None
        self._started_process = False
        self._lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    async def start(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._start_sync)

    def _start_sync(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        if not self.service_path.exists():
            raise WhatsAppBridgeError(f"WhatsApp bridge entrypoint not found at {self.service_path}")

        env = os.environ.copy()
        env.setdefault("TP_WHATSAPP_HOST", self.host)
        env.setdefault("TP_WHATSAPP_PORT", str(self.port))
        env.setdefault("TP_WHATSAPP_CLOUD_LABEL", self.cloud_label_name)
        env.setdefault("TP_WHATSAPP_AUTH_DIR", str(self.auth_dir))

        project_root = self.service_path.parents[1]
        self.process = subprocess.Popen(
            [self.node_bin, str(self.service_path)],
            cwd=str(project_root),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self._started_process = True
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise WhatsAppBridgeError("WhatsApp bridge exited during startup")
            try:
                self._request_sync("GET", "/health")
                return
            except WhatsAppBridgeError:
                time.sleep(0.2)
        raise WhatsAppBridgeError(f"WhatsApp bridge did not become healthy at {self.base_url}/health")

    async def stop(self) -> None:
        async with self._lock:
            process = self.process
            self.process = None
            if process is None or not self._started_process:
                return
            if process.poll() is None:
                process.terminate()
                try:
                    await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await asyncio.to_thread(process.wait)

    async def get_status(self, *, limit: int = 200) -> dict[str, object]:
        return await self._request("GET", f"/api/status?limit={int(limit)}")

    async def get_chats(self, *, limit: int = 200) -> dict[str, object]:
        return await self._request("GET", f"/api/chats?limit={int(limit)}")

    async def get_chat(self, jid: str, *, limit: int = 50) -> dict[str, object]:
        encoded_jid = urlparse.quote(str(jid), safe="@.-_")
        return await self._request("GET", f"/api/chat?jid={encoded_jid}&limit={int(limit)}")

    async def get_updates(self, *, limit: int = 50) -> dict[str, object]:
        return await self._request("GET", f"/api/updates?limit={int(limit)}")

    async def request_pairing_code(self, *, phone: str) -> dict[str, object]:
        return await self._request("POST", "/api/auth/request-pairing-code", {"phone": phone})

    async def logout(self) -> dict[str, object]:
        return await self._request("POST", "/api/auth/logout", {})

    async def send_message(self, *, jid: str, text: str) -> dict[str, object]:
        return await self._request("POST", "/api/send-message", {"jid": jid, "text": text})

    async def mark_read(self, *, jid: str, message_id: str | None = None) -> dict[str, object]:
        payload: dict[str, object] = {"jid": jid}
        if message_id:
            payload["message_id"] = message_id
        return await self._request("POST", "/api/mark-read", payload)

    async def _request(self, method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        async with self._lock:
            if self.process is None or self.process.poll() is not None:
                await asyncio.to_thread(self._start_sync)
            return await asyncio.to_thread(self._request_sync, method, path, payload)

    def _request_sync(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urlrequest.Request(f"{self.base_url}{path}", data=body, method=method, headers=headers)
        try:
            with urlrequest.urlopen(req, timeout=10) as response:
                raw = response.read().decode("utf-8")
        except urlerror.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = {"error": raw or exc.reason}
            raise WhatsAppBridgeError(str(parsed.get("error") or exc.reason)) from exc
        except OSError as exc:
            raise WhatsAppBridgeError(str(exc) or exc.__class__.__name__) from exc

        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise WhatsAppBridgeError(f"WhatsApp bridge returned invalid JSON: {exc}") from exc

    async def close(self) -> None:
        await self.stop()

    async def __aenter__(self) -> "WhatsAppBridge":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
