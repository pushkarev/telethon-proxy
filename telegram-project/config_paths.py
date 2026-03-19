from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_CONFIG_HOME = Path(os.path.expanduser("~/.tlt-proxy"))
DEFAULT_ENV_PATH = DEFAULT_CONFIG_HOME / ".env"


def load_project_env() -> Path:
    """Load the default proxy env file from ~/.tlt-proxy/.env if it exists.

    Falls back to the process environment when the file is absent.
    Returns the resolved default env path so callers can display it.
    """
    load_dotenv(dotenv_path=DEFAULT_ENV_PATH)
    return DEFAULT_ENV_PATH


def config_home() -> Path:
    DEFAULT_CONFIG_HOME.mkdir(parents=True, exist_ok=True)
    return DEFAULT_CONFIG_HOME
