from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from config_paths import DEFAULT_CONFIG_HOME, config_home
from .secrets_store import MacOSSecretStore


@dataclass(slots=True)
class ProxyConfig:
    control_host: str = "127.0.0.1"
    control_port: int = 9000
    mtproto_enabled: bool = True
    mtproto_host: str = "127.0.0.1"
    mtproto_port: int = 9001
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8788
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8791
    mcp_scheme: str = "http"
    mcp_path: str = "/mcp"
    mcp_token: str = ""
    mcp_tls_cert_name: str = ""
    mcp_tls_key_name: str = ""
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
    mcp_settings_name: str = str(DEFAULT_CONFIG_HOME / "mcp_settings.json")
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
    imessage_enabled: bool = False
    imessage_messages_app_accessible: bool = False
    imessage_database_accessible: bool = False
    imessage_db_name: str = str(Path.home() / "Library" / "Messages" / "chat.db")
    imessage_settings_name: str = str(DEFAULT_CONFIG_HOME / "imessage_settings.json")
    imessage_visible_chats_name: str = str(DEFAULT_CONFIG_HOME / "imessage_visible_chats.json")

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

    @property
    def mcp_settings_path(self) -> Path:
        path = Path(self.mcp_settings_name).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def mcp_tls_cert_path(self) -> Path | None:
        value = self.mcp_tls_cert_name.strip()
        if not value:
            return None
        return Path(value).expanduser()

    @property
    def mcp_tls_key_path(self) -> Path | None:
        value = self.mcp_tls_key_name.strip()
        if not value:
            return None
        return Path(value).expanduser()

    @property
    def mcp_endpoint(self) -> str:
        return f"{self.mcp_scheme}://{self.mcp_host}:{self.mcp_port}{self.mcp_path}"

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

    @property
    def imessage_db_path(self) -> Path:
        return Path(self.imessage_db_name).expanduser()

    @property
    def imessage_settings_path(self) -> Path:
        path = Path(self.imessage_settings_name).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def imessage_visible_chats_path(self) -> Path:
        path = Path(self.imessage_visible_chats_name).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def save_mcp_settings(self) -> None:
        payload = {
            "host": self.mcp_host,
            "port": self.mcp_port,
            "scheme": self.mcp_scheme,
        }
        tmp_path = self.mcp_settings_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp_path.replace(self.mcp_settings_path)

    def validate_mcp_tls_config(self) -> None:
        if self.mcp_scheme != "https":
            return
        cert_path = self.mcp_tls_cert_path
        key_path = self.mcp_tls_key_path
        if cert_path is None or key_path is None:
            raise ValueError("HTTPS requires TP_MCP_TLS_CERT and TP_MCP_TLS_KEY to be set.")
        if not cert_path.exists():
            raise ValueError(f"MCP TLS certificate file does not exist: {cert_path}")
        if not key_path.exists():
            raise ValueError(f"MCP TLS private key file does not exist: {key_path}")

    def save_imessage_settings(self) -> None:
        payload = {
            "enabled": self.imessage_enabled,
            "messages_app_accessible": self.imessage_messages_app_accessible,
            "database_accessible": self.imessage_database_accessible,
        }
        tmp_path = self.imessage_settings_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp_path.replace(self.imessage_settings_path)

    @classmethod
    def from_env(cls) -> "ProxyConfig":
        secret_store = MacOSSecretStore()
        saved = secret_store.load_upstream_secrets() if secret_store.is_available else None
        mcp_saved = cls._load_mcp_settings()
        imessage_saved = cls._load_imessage_settings()
        upstream_api_id = saved.api_id if saved and saved.api_id else os.getenv("TG_API_ID", "0")
        upstream_api_hash = saved.api_hash if saved and saved.api_hash else os.getenv("TG_API_HASH", "")
        mcp_token, mcp_token_env_managed = secret_store.load_or_create_mcp_token(
            env_token=os.getenv("TP_MCP_TOKEN", ""),
            legacy_path=DEFAULT_CONFIG_HOME / "mcp_token",
        )
        mcp_scheme = os.getenv("TP_MCP_SCHEME", str(mcp_saved.get("scheme", "http"))).strip().lower() or "http"
        if mcp_scheme not in {"http", "https"}:
            mcp_scheme = "http"

        return cls(
            control_host=os.getenv("TP_CONTROL_HOST", os.getenv("TP_LISTEN_HOST", "127.0.0.1")),
            control_port=int(os.getenv("TP_CONTROL_PORT", os.getenv("TP_LISTEN_PORT", "9000"))),
            mtproto_enabled=os.getenv("TP_MTPROTO_ENABLED", "1") not in {"0", "false", "False"},
            mtproto_host=os.getenv("TP_MTPROTO_HOST", "127.0.0.1"),
            mtproto_port=int(os.getenv("TP_MTPROTO_PORT", "9001")),
            dashboard_host=os.getenv("TP_DASHBOARD_HOST", "127.0.0.1"),
            dashboard_port=int(os.getenv("TP_DASHBOARD_PORT", "8788")),
            mcp_host=os.getenv("TP_MCP_HOST", mcp_saved.get("host", "127.0.0.1")),
            mcp_port=int(os.getenv("TP_MCP_PORT", str(mcp_saved.get("port", 8791)))),
            mcp_scheme=mcp_scheme,
            mcp_path=os.getenv("TP_MCP_PATH", "/mcp"),
            mcp_token=mcp_token,
            mcp_token_env_managed=mcp_token_env_managed,
            mcp_tls_cert_name=os.getenv("TP_MCP_TLS_CERT", ""),
            mcp_tls_key_name=os.getenv("TP_MCP_TLS_KEY", ""),
            downstream_host=os.getenv("TP_DOWNSTREAM_HOST", os.getenv("TP_MTPROTO_HOST", "127.0.0.1")),
            downstream_api_id=int(os.getenv("TP_DOWNSTREAM_API_ID", "900000")),
            downstream_api_hash=os.getenv("TP_DOWNSTREAM_API_HASH", "dev-proxy-change-me"),
            downstream_login_code=os.getenv("TP_DOWNSTREAM_LOGIN_CODE", "00000"),
            downstream_password=os.getenv("TP_DOWNSTREAM_PASSWORD", ""),
            downstream_session_label=os.getenv("TP_DOWNSTREAM_SESSION_LABEL", "proxy"),
            upstream_api_id=int(upstream_api_id),
            upstream_api_hash=upstream_api_hash,
            upstream_phone=os.getenv("TG_PHONE", saved.phone if saved else ""),
            upstream_session_string=os.getenv("TP_UPSTREAM_SESSION_STRING", saved.session if saved else ""),
            upstream_session_name=os.getenv("TP_UPSTREAM_SESSION", str(DEFAULT_CONFIG_HOME / "sessions/proxy_upstream")),
            downstream_registry_name=os.getenv(
                "TP_DOWNSTREAM_REGISTRY",
                str(DEFAULT_CONFIG_HOME / "downstream_registry.json"),
            ),
            mcp_settings_name=os.getenv("TP_MCP_SETTINGS", str(DEFAULT_CONFIG_HOME / "mcp_settings.json")),
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
            imessage_enabled=os.getenv(
                "TP_IMESSAGE_ENABLED",
                "1" if bool(imessage_saved.get("enabled")) else "0",
            ) not in {"0", "false", "False"},
            imessage_messages_app_accessible=bool(imessage_saved.get("messages_app_accessible")),
            imessage_database_accessible=bool(imessage_saved.get("database_accessible")),
            imessage_db_name=os.getenv("TP_IMESSAGE_DB", str(Path.home() / "Library" / "Messages" / "chat.db")),
            imessage_settings_name=os.getenv(
                "TP_IMESSAGE_SETTINGS",
                str(DEFAULT_CONFIG_HOME / "imessage_settings.json"),
            ),
            imessage_visible_chats_name=os.getenv(
                "TP_IMESSAGE_VISIBLE_CHATS",
                str(DEFAULT_CONFIG_HOME / "imessage_visible_chats.json"),
            ),
        )

    @staticmethod
    def _load_mcp_settings() -> dict[str, object]:
        path = Path(os.getenv("TP_MCP_SETTINGS", str(config_home() / "mcp_settings.json"))).expanduser()
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        host = str(payload.get("host") or "").strip()
        port = payload.get("port")
        result: dict[str, object] = {}
        if host:
            result["host"] = host
        if isinstance(port, int):
            result["port"] = port
        scheme = str(payload.get("scheme") or "").strip().lower()
        if scheme in {"http", "https"}:
            result["scheme"] = scheme
        return result

    @staticmethod
    def _load_imessage_settings() -> dict[str, object]:
        path = Path(os.getenv("TP_IMESSAGE_SETTINGS", str(config_home() / "imessage_settings.json"))).expanduser()
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        enabled = payload.get("enabled")
        messages_app_accessible = payload.get("messages_app_accessible")
        database_accessible = payload.get("database_accessible")
        result: dict[str, object] = {}
        if isinstance(enabled, bool):
            result["enabled"] = enabled
        if isinstance(messages_app_accessible, bool):
            result["messages_app_accessible"] = messages_app_accessible
        if isinstance(database_accessible, bool):
            result["database_accessible"] = database_accessible
        return result
