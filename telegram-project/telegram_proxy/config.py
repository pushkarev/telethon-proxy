from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ProxyConfig:
    listen_host: str = "127.0.0.1"
    listen_port: int = 9000
    downstream_api_id: int = 900000
    downstream_api_hash: str = "dev-proxy-change-me"
    downstream_login_code: str = "00000"
    downstream_password: str = ""
    upstream_api_id: int = 0
    upstream_api_hash: str = ""
    upstream_phone: str = ""
    upstream_session_name: str = "sessions/proxy_upstream"
    cloud_folder_name: str = "Cloud"
    allow_member_listing: bool = True
    update_buffer_size: int = 1000
    incoming_hook_command: str = ""

    @property
    def upstream_session_path(self) -> Path:
        path = Path(self.upstream_session_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def from_env(cls) -> "ProxyConfig":
        return cls(
            listen_host=os.getenv("TP_LISTEN_HOST", "127.0.0.1"),
            listen_port=int(os.getenv("TP_LISTEN_PORT", "9000")),
            downstream_api_id=int(os.getenv("TP_DOWNSTREAM_API_ID", "900000")),
            downstream_api_hash=os.getenv("TP_DOWNSTREAM_API_HASH", "dev-proxy-change-me"),
            downstream_login_code=os.getenv("TP_DOWNSTREAM_LOGIN_CODE", "00000"),
            downstream_password=os.getenv("TP_DOWNSTREAM_PASSWORD", ""),
            upstream_api_id=int(os.getenv("TG_API_ID", "0")),
            upstream_api_hash=os.getenv("TG_API_HASH", ""),
            upstream_phone=os.getenv("TG_PHONE", ""),
            upstream_session_name=os.getenv("TP_UPSTREAM_SESSION", "sessions/proxy_upstream"),
            cloud_folder_name=os.getenv("TP_CLOUD_FOLDER", "Cloud"),
            allow_member_listing=os.getenv("TP_ALLOW_MEMBER_LISTING", "1") not in {"0", "false", "False"},
            update_buffer_size=int(os.getenv("TP_UPDATE_BUFFER_SIZE", "1000")),
            incoming_hook_command=os.getenv("TP_INCOMING_HOOK", ""),
        )
