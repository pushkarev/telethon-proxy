from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
from typing import Any

from config_paths import load_project_env
from telegram_proxy.config import ProxyConfig
from telegram_proxy.mcp_service import SERVER_PROTOCOL_VERSION


class McpClientError(RuntimeError):
    pass


class McpClient:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        path: str,
        token: str,
        timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.path = path
        self.token = token
        self.timeout = timeout
        self.session_id: str | None = None
        self._rpc_id = 0

    def initialize(self, *, client_name: str = "list-mcp-chats", protocol_version: str = SERVER_PROTOCOL_VERSION) -> dict[str, Any]:
        status, payload, headers = self._request_json(
            "POST",
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": protocol_version,
                    "capabilities": {},
                    "clientInfo": {"name": client_name},
                },
            },
            include_session=False,
        )
        self._expect_status(status, payload, expected=200)
        self.session_id = headers.get("mcp-session-id")
        if not self.session_id:
            raise McpClientError("MCP server did not return an Mcp-Session-Id header")
        return self._extract_result(payload)

    def notify_initialized(self) -> None:
        status, payload, _headers = self._request_json(
            "POST",
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "notifications/initialized",
            },
        )
        self._expect_status(status, payload, expected=202)

    def list_chats(self, *, limit: int = 100) -> list[dict[str, Any]]:
        result = self.call_tool("telegram.list_chats", {"limit": limit})
        chats = result.get("chats")
        if not isinstance(chats, list):
            raise McpClientError("MCP server returned an invalid chat list")
        return chats

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        status, payload, _headers = self._request_json(
            "POST",
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {
                    "name": name,
                    "arguments": arguments or {},
                },
            },
        )
        self._expect_status(status, payload, expected=200)
        result = self._extract_result(payload)
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise McpClientError("MCP tool response did not include structuredContent")
        if result.get("isError"):
            raise McpClientError(structured.get("error", f"MCP tool {name} failed"))
        return structured

    def close(self) -> None:
        if not self.session_id:
            return
        try:
            self._request_json("DELETE", None)
        finally:
            self.session_id = None

    def _request_json(
        self,
        method: str,
        payload: dict[str, Any] | None,
        *,
        include_session: bool = True,
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if include_session and self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            connection.request(method, self.path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            parsed_headers = {name.lower(): value for name, value in response.getheaders()}
        except OSError as exc:
            raise McpClientError(f"Could not reach MCP server at http://{self.host}:{self.port}{self.path}: {exc}") from exc
        finally:
            connection.close()

        if not raw:
            return response.status, {}, parsed_headers
        try:
            payload_data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise McpClientError(f"MCP server returned invalid JSON: {exc}") from exc
        if not isinstance(payload_data, dict):
            raise McpClientError("MCP server returned a non-object JSON response")
        return response.status, payload_data, parsed_headers

    def _expect_status(self, status: int, payload: dict[str, Any], *, expected: int) -> None:
        if status == expected:
            return
        message = payload.get("error")
        if isinstance(message, dict):
            message = message.get("message")
        if not message:
            message = f"Unexpected HTTP status {status}"
        raise McpClientError(str(message))

    def _extract_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "error" in payload:
            error = payload["error"]
            if isinstance(error, dict):
                message = error.get("message", "Unknown JSON-RPC error")
            else:
                message = str(error)
            raise McpClientError(message)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise McpClientError("MCP server response did not include a JSON-RPC result object")
        return result

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id


def parse_args(config: ProxyConfig) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Connect to the local MCP endpoint and list accessible Telegram chats.")
    parser.add_argument("--host", default=os.getenv("TP_MCP_HOST", config.mcp_host))
    parser.add_argument("--port", type=int, default=int(os.getenv("TP_MCP_PORT", str(config.mcp_port))))
    parser.add_argument("--path", default=os.getenv("TP_MCP_PATH", config.mcp_path))
    parser.add_argument("--token", default=os.getenv("TP_MCP_TOKEN", config.mcp_token))
    parser.add_argument("--limit", type=int, default=int(os.getenv("TP_MCP_DIALOG_LIMIT", "100")))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("TP_MCP_TIMEOUT", "10")))
    parser.add_argument("--client-name", default="list-mcp-chats")
    parser.add_argument("--protocol-version", default=SERVER_PROTOCOL_VERSION)
    parser.add_argument("--json", action="store_true", help="Print raw JSON instead of tab-separated rows.")
    return parser.parse_args()


def render_chats(chats: list[dict[str, Any]]) -> str:
    lines = []
    for chat in chats:
        peer_id = str(chat.get("peer_id", ""))
        kind = str(chat.get("kind", ""))
        title = str(chat.get("title", ""))
        username = str(chat.get("username") or "-")
        lines.append(f"{peer_id}\t{kind}\t{title}\t{username}")
    return "\n".join(lines)


def main() -> int:
    load_project_env()
    config = ProxyConfig.from_env()
    args = parse_args(config)
    client = McpClient(
        host=args.host,
        port=args.port,
        path=args.path,
        token=args.token,
        timeout=args.timeout,
    )
    try:
        client.initialize(client_name=args.client_name, protocol_version=args.protocol_version)
        client.notify_initialized()
        chats = client.list_chats(limit=args.limit)
    except McpClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()

    if args.json:
        print(json.dumps({"ok": True, "chats": chats}, ensure_ascii=False, indent=2))
        return 0

    output = render_chats(chats)
    if output:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
