from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from config_paths import DEFAULT_CONFIG_HOME
from .secrets_store import MacOSSecretStore


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
    upstream_session_string: str = ""
    upstream_session_name: str = str(DEFAULT_CONFIG_HOME / "sessions/proxy_upstream")
    downstream_registry_name: str = str(DEFAULT_CONFIG_HOME / "downstream_registry.json")
    cloud_folder_name: str = "Cloud"
    allow_member_listing: bool = True
    update_buffer_size: int = 1000
    incoming_hook_command: str = ""
    upstream_reconnect_min_delay: float = 2.0
    upstream_reconnect_max_delay: float = 30.0
    mcp_token_env_managed: bool = False
    whatsapp_host: str = "127.0.0.1"
    whatsapp_port: int = 8792
    whatsapp_cloud_label_name: str = "Cloud"
    whatsapp_auth_dir: str = str(DEFAULT_CONFIG_HOME / "whatsapp-auth")

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

    def upstream_session_candidates(self, *, session_path: Path | None = None) -> list[Path]:
        base = (session_path or self.upstream_session_path).expanduser()
        return [
            base,
            Path(f"{base}.session"),
            Path(f"{base}.session-journal"),
            Path(f"{base}.session-shm"),
            Path(f"{base}.session-wal"),
        ]

    def has_upstream_session_material(self, *, session_path: Path | None = None) -> bool:
        if self.upstream_session_string:
            return True
        return any(path.exists() for path in self.upstream_session_candidates(session_path=session_path))

    @property
    def whatsapp_auth_path(self) -> Path:
        path = Path(self.whatsapp_auth_dir).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def from_env(cls) -> "ProxyConfig":
        secret_store = MacOSSecretStore()
        saved = secret_store.load_upstream_secrets() if secret_store.is_available else None
        mcp_token, mcp_token_env_managed = secret_store.load_or_create_mcp_token(
            env_token=os.getenv("TP_MCP_TOKEN", ""),
            legacy_path=DEFAULT_CONFIG_HOME / "mcp_token",
        )
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
            mcp_token=mcp_token,
            mcp_token_env_managed=mcp_token_env_managed,
            downstream_host=os.getenv("TP_DOWNSTREAM_HOST", os.getenv("TP_MTPROTO_HOST", "127.0.0.1")),
            downstream_api_id=int(os.getenv("TP_DOWNSTREAM_API_ID", "900000")),
            downstream_api_hash=os.getenv("TP_DOWNSTREAM_API_HASH", "dev-proxy-change-me"),
            downstream_login_code=os.getenv("TP_DOWNSTREAM_LOGIN_CODE", "00000"),
            downstream_password=os.getenv("TP_DOWNSTREAM_PASSWORD", ""),
            downstream_session_label=os.getenv("TP_DOWNSTREAM_SESSION_LABEL", "proxy"),
            upstream_api_id=int(os.getenv("TG_API_ID", saved.api_id if saved and saved.api_id else "0")),
            upstream_api_hash=os.getenv("TG_API_HASH", saved.api_hash if saved else ""),
            upstream_phone=os.getenv("TG_PHONE", saved.phone if saved else ""),
            upstream_session_string=os.getenv("TP_UPSTREAM_SESSION_STRING", saved.session if saved else ""),
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
            whatsapp_host=os.getenv("TP_WHATSAPP_HOST", "127.0.0.1"),
            whatsapp_port=int(os.getenv("TP_WHATSAPP_PORT", "8792")),
            whatsapp_cloud_label_name=os.getenv("TP_WHATSAPP_CLOUD_LABEL", os.getenv("TP_CLOUD_FOLDER", "Cloud")),
            whatsapp_auth_dir=os.getenv("TP_WHATSAPP_AUTH_DIR", str(DEFAULT_CONFIG_HOME / "whatsapp-auth")),
        )
