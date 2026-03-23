from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import sys
from getpass import getuser
from pathlib import Path

from config_paths import load_project_env
from telegram_proxy.config import ProxyConfig
from telegram_proxy.service import ProxyService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local messaging bridge service.")
    parser.add_argument("--print-launchd-plist", action="store_true", help="Print a launchd plist for this service and exit.")
    parser.add_argument("--install-launchd", action="store_true", help="Install and start the macOS launchd service.")
    parser.add_argument("--uninstall-launchd", action="store_true", help="Stop and remove the macOS launchd service.")
    parser.add_argument("--launchd-status", action="store_true", help="Print launchd status for this service label.")
    parser.add_argument("--launchd-label", default="dev.telethon-proxy", help="launchd service label.")
    return parser


def render_launchd_plist(config: ProxyConfig, *, label: str) -> str:
    project_dir = Path(__file__).resolve().parent
    program = [sys.executable, str(project_dir / "proxy_service.py")]
    env_path = Path.home() / ".tlt-proxy/.env"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{program[0]}</string>
    <string>{program[1]}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{project_dir}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
    <key>TG_ENV_FILE</key>
    <string>{env_path}</string>
  </dict>
  <key>StandardOutPath</key>
  <string>{Path.home() / 'Library/Logs/telethon-proxy.log'}</string>
  <key>StandardErrorPath</key>
  <string>{Path.home() / 'Library/Logs/telethon-proxy.log'}</string>
</dict>
</plist>"""


def launch_agent_path(label: str) -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{label}.plist"


def launchctl_target(label: str) -> str:
    return f"gui/{os.getuid()}/{label}"


def run_launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *args],
        check=check,
        text=True,
        capture_output=True,
    )


def install_launchd(config: ProxyConfig, *, label: str) -> int:
    plist_path = launch_agent_path(label)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(render_launchd_plist(config, label=label), encoding="utf-8")

    target = launchctl_target(label)
    run_launchctl("bootout", target, check=False)
    run_launchctl("bootstrap", f"gui/{os.getuid()}", str(plist_path))
    run_launchctl("enable", target)
    run_launchctl("kickstart", "-k", target)

    print(f"installed={plist_path}")
    print(f"target={target}")
    print(f"user={getuser()}")
    print(f"dashboard={config.dashboard_host}:{config.dashboard_port}")
    print(f"mcp={config.mcp_endpoint}")
    return 0


def uninstall_launchd(*, label: str) -> int:
    plist_path = launch_agent_path(label)
    target = launchctl_target(label)
    run_launchctl("bootout", target, check=False)
    if plist_path.exists():
        plist_path.unlink()
    print(f"removed={plist_path}")
    print(f"target={target}")
    return 0


def print_launchd_status(*, label: str) -> int:
    target = launchctl_target(label)
    result = run_launchctl("print", target, check=False)
    if result.returncode != 0:
        print(f"target={target}")
        print("status=not-loaded")
        if result.stderr.strip():
            print(result.stderr.strip())
        return result.returncode
    print(result.stdout.rstrip())
    return 0


async def amain() -> int:
    load_project_env()
    args = build_parser().parse_args()
    config = ProxyConfig.from_env()

    if args.print_launchd_plist:
        print(render_launchd_plist(config, label=args.launchd_label))
        return 0
    if args.install_launchd:
        return install_launchd(config, label=args.launchd_label)
    if args.uninstall_launchd:
        return uninstall_launchd(label=args.launchd_label)
    if args.launchd_status:
        return print_launchd_status(label=args.launchd_label)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    service = ProxyService(config)
    await service.start()
    try:
        await service.serve_forever()
    finally:
        await service.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
