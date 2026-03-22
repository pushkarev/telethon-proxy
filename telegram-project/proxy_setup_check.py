from __future__ import annotations

from config_paths import load_project_env
from telegram_proxy.config import ProxyConfig


def main() -> None:
    env_path = load_project_env()
    config = ProxyConfig.from_env()

    print('Proxy setup check')
    print('-----------------')
    print(f'Env file: {env_path}')
    print(f'Control server: {config.control_host}:{config.control_port}')
    print(f'MTProto endpoint: {config.mtproto_host}:{config.mtproto_port}')
    print(f'Cloud folder: {config.cloud_folder_name}')
    print(f'Upstream session: {config.upstream_session_path}')
    print(f'Downstream registry: {config.downstream_registry_path}')
    print(f'Member listing allowed: {config.allow_member_listing}')
    print(f'Buffer size: {config.update_buffer_size}')
    print()

    missing = []
    if not config.upstream_api_id:
        missing.append('TG_API_ID')
    if not config.upstream_api_hash:
        missing.append('TG_API_HASH')
    if not config.upstream_phone:
        missing.append('TG_PHONE')

    if missing:
        print('Missing required upstream credentials:')
        for name in missing:
            print(f' - {name}')
        raise SystemExit(1)

    print('Upstream credentials present.')
    print('Next steps:')
    print('  1. python app.py')
    print('  2. python list_chat_folders.py')
    print('  3. python proxy_service.py')
    print('  4. python proxy_service.py --issue-session')


if __name__ == '__main__':
    main()
