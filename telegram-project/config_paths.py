from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - exercised implicitly in tests without python-dotenv installed
    def load_dotenv(*, dotenv_path=None):
        if not dotenv_path:
            return False
        path = Path(dotenv_path).expanduser()
        if not path.exists():
            return False
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip())
        return True

DEFAULT_CONFIG_HOME = Path(os.path.expanduser("~/.tlt-proxy"))
DEFAULT_ENV_PATH = DEFAULT_CONFIG_HOME / ".env"


def load_project_env() -> Path:
    """Load the default proxy env file from ~/.tlt-proxy/.env if it exists.

    Falls back to the process environment when the file is absent.
    Returns the resolved default env path so callers can display it.
    """
    env_path = Path(os.getenv("TG_ENV_FILE", DEFAULT_ENV_PATH)).expanduser()
    load_dotenv(dotenv_path=env_path)
    return env_path


def config_home() -> Path:
    DEFAULT_CONFIG_HOME.mkdir(parents=True, exist_ok=True)
    return DEFAULT_CONFIG_HOME
