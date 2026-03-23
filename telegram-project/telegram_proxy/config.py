from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from config_paths import DEFAULT_CONFIG_HOME, config_home


def _load_or_create_local_secret(env_name: str, default_path: Path) -> str:
    value = os.getenv(env_name, "").strip()
    if value:
        return value
    path = Path(os.getenv(f"{env_name}_FILE", str(default_path))).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    token = secrets.token_urlsafe(32)
    path.write_text(token + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return token


@dataclass(slots=True)
class ProxyConfig:
    control_host: str = "127.0.0.1"
    control_port: int = 9000
    mtproto_host: str = "127.0.0.1"
    mtproto_port: int = 9001
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8788
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8791
    mcp_path: str = "/mcp"
    mcp_token: str = ""
    downstream_host: str = "127.0.0.1"
    downstream_api_id: int = 900000
    downstream_api_hash: str = "dev-proxy-change-me"
    downstream_login_code: str = "00000"
    downstream_password: str = ""
    downstream_session_label: str = "proxy"
    upstream_api_id: int = 0
    upstream_api_hash: str = ""
    upstream_phone: str = ""
    upstream_session_name: str = str(DEFAULT_CONFIG_HOME / "sessions/proxy_upstream")
    downstream_registry_name: str = str(DEFAULT_CONFIG_HOME / "downstream_registry.json")
    cloud_folder_name: str = "Cloud"
    allow_member_listing: bool = True
    update_buffer_size: int = 1000
    incoming_hook_command: str = ""
    upstream_reconnect_min_delay: float = 2.0
    upstream_reconnect_max_delay: float = 30.0

    @property
    def listen_host(self) -> str:
        return self.control_host

    @property
    def listen_port(self) -> int:
        return self.control_port

    @property
    def upstream_session_path(self) -> Path:
        path = Path(self.upstream_session_name).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def downstream_registry_path(self) -> Path:
        path = Path(self.downstream_registry_name).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def from_env(cls) -> "ProxyConfig":
        return cls(
            control_host=os.getenv("TP_CONTROL_HOST", os.getenv("TP_LISTEN_HOST", "127.0.0.1")),
            control_port=int(os.getenv("TP_CONTROL_PORT", os.getenv("TP_LISTEN_PORT", "9000"))),
            mtproto_host=os.getenv("TP_MTPROTO_HOST", "127.0.0.1"),
            mtproto_port=int(os.getenv("TP_MTPROTO_PORT", "9001")),
            dashboard_host=os.getenv("TP_DASHBOARD_HOST", "127.0.0.1"),
            dashboard_port=int(os.getenv("TP_DASHBOARD_PORT", "8788")),
            mcp_host=os.getenv("TP_MCP_HOST", "127.0.0.1"),
            mcp_port=int(os.getenv("TP_MCP_PORT", "8791")),
            mcp_path=os.getenv("TP_MCP_PATH", "/mcp"),
            mcp_token=_load_or_create_local_secret("TP_MCP_TOKEN", DEFAULT_CONFIG_HOME / "mcp_token"),
            downstream_host=os.getenv("TP_DOWNSTREAM_HOST", os.getenv("TP_MTPROTO_HOST", "127.0.0.1")),
            downstream_api_id=int(os.getenv("TP_DOWNSTREAM_API_ID", "900000")),
            downstream_api_hash=os.getenv("TP_DOWNSTREAM_API_HASH", "dev-proxy-change-me"),
            downstream_login_code=os.getenv("TP_DOWNSTREAM_LOGIN_CODE", "00000"),
            downstream_password=os.getenv("TP_DOWNSTREAM_PASSWORD", ""),
            downstream_session_label=os.getenv("TP_DOWNSTREAM_SESSION_LABEL", "proxy"),
            upstream_api_id=int(os.getenv("TG_API_ID", "0")),
            upstream_api_hash=os.getenv("TG_API_HASH", ""),
            upstream_phone=os.getenv("TG_PHONE", ""),
            upstream_session_name=os.getenv("TP_UPSTREAM_SESSION", str(DEFAULT_CONFIG_HOME / "sessions/proxy_upstream")),
            downstream_registry_name=os.getenv(
                "TP_DOWNSTREAM_REGISTRY",
                str(DEFAULT_CONFIG_HOME / "downstream_registry.json"),
            ),
            cloud_folder_name=os.getenv("TP_CLOUD_FOLDER", "Cloud"),
            allow_member_listing=os.getenv("TP_ALLOW_MEMBER_LISTING", "1") not in {"0", "false", "False"},
            update_buffer_size=int(os.getenv("TP_UPDATE_BUFFER_SIZE", "1000")),
            incoming_hook_command=os.getenv("TP_INCOMING_HOOK", ""),
            upstream_reconnect_min_delay=float(os.getenv("TP_UPSTREAM_RECONNECT_MIN_DELAY", "2")),
            upstream_reconnect_max_delay=float(os.getenv("TP_UPSTREAM_RECONNECT_MAX_DELAY", "30")),
        )
