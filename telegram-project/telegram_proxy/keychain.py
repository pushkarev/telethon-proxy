from .secrets_store import (
    KEYCHAIN_SERVICE,
    MCP_TOKEN_ACCOUNT,
    UPSTREAM_API_HASH_ACCOUNT,
    UPSTREAM_API_ID_ACCOUNT,
    UPSTREAM_PHONE_ACCOUNT,
    UPSTREAM_SESSION_ACCOUNT,
    MacOSSecretStore,
    SecretStoreError,
    UpstreamSecrets,
)


KeychainError = SecretStoreError
MacOSKeychainStore = MacOSSecretStore
