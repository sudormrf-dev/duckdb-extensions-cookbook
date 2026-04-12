"""DuckDB secret manager patterns (DuckDB >= 0.10).

DuckDB's secret manager stores credentials for httpfs, S3, GCS, Azure, etc.
Secrets are scoped to a session or persisted to disk.

Patterns:
  - SecretType: credential type (S3, GCS, R2, Azure, etc.)
  - ProviderType: how credentials are resolved
  - SecretScope: persistence scope
  - SecretConfig: full secret definition
  - SecretManager: builds CREATE SECRET SQL statements

Usage::

    cfg = SecretConfig(
        name="my_s3",
        secret_type=SecretType.S3,
        provider=ProviderType.CONFIG,
        key_id="AKID...",
        secret="...",
        region="us-east-1",
    )
    mgr = SecretManager()
    sql = mgr.create_secret_sql(cfg)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SecretType(str, Enum):
    """Supported DuckDB secret types."""

    S3 = "S3"
    GCS = "GCS"
    R2 = "R2"  # Cloudflare R2 (S3-compatible)
    AZURE = "AZURE"
    HUGGINGFACE = "HUGGINGFACE"
    GENERIC_S3 = "S3"  # alias


class ProviderType(str, Enum):
    """How credentials are resolved."""

    CONFIG = "config"  # Explicit key/secret in the statement
    CREDENTIAL_CHAIN = "credential_chain"  # Auto-detect (env, instance, config file)
    ENV = "env"  # From environment variables
    INSTANCE = "instance"  # From cloud provider instance metadata


class SecretScope(str, Enum):
    """Persistence scope for secrets."""

    SESSION = "session"  # Lost when connection closes
    PERMANENT = "permanent"  # Persisted to disk (~/.duckdb/stored_secrets/)


@dataclass
class SecretConfig:
    """Configuration for a DuckDB secret.

    Attributes:
        name: Secret name (identifier in SQL).
        secret_type: Credential type.
        provider: How to resolve credentials.
        scope: Persistence scope.
        key_id: Access key ID (CONFIG provider).
        secret: Secret access key (CONFIG provider).
        region: Cloud region.
        endpoint: Custom endpoint URL (MinIO, R2, etc.).
        session_token: Optional session token.
        extra_params: Additional provider-specific parameters.
    """

    name: str
    secret_type: SecretType = SecretType.S3
    provider: ProviderType = ProviderType.CONFIG
    scope: SecretScope = SecretScope.SESSION
    key_id: str = ""
    secret: str = ""
    region: str = ""
    endpoint: str = ""
    session_token: str = ""
    extra_params: dict[str, str] = field(default_factory=dict)

    def has_credentials(self) -> bool:
        """Return True if explicit credentials are set."""
        return bool(self.key_id and self.secret)

    def is_persistent(self) -> bool:
        """Return True if the secret persists across sessions."""
        return self.scope == SecretScope.PERMANENT

    def redacted(self) -> SecretConfig:
        """Return a copy with credentials redacted."""
        return SecretConfig(
            name=self.name,
            secret_type=self.secret_type,
            provider=self.provider,
            scope=self.scope,
            key_id="***" if self.key_id else "",
            secret="***" if self.secret else "",
            region=self.region,
            endpoint=self.endpoint,
            session_token="***" if self.session_token else "",
        )


class SecretManager:
    """Builds DuckDB CREATE/DROP SECRET SQL statements.

    Secrets enable transparent authentication for remote file access.
    """

    def __init__(self) -> None:
        self._secrets: dict[str, SecretConfig] = {}

    def add(self, config: SecretConfig) -> SecretManager:
        """Register a secret configuration."""
        self._secrets[config.name] = config
        return self

    def get(self, name: str) -> SecretConfig | None:
        """Return a secret config by name."""
        return self._secrets.get(name)

    def names(self) -> list[str]:
        """Return names of all registered secrets."""
        return list(self._secrets.keys())

    def create_secret_sql(self, config: SecretConfig) -> str:
        """Generate CREATE SECRET SQL for the given config.

        Args:
            config: Secret configuration.

        Returns:
            DuckDB CREATE SECRET SQL statement.
        """
        or_replace = "OR REPLACE " if config.name in self._secrets else ""
        persistent = "PERSISTENT " if config.is_persistent() else ""
        params: list[str] = [f"TYPE {config.secret_type.value}"]
        params.append(f"PROVIDER {config.provider.value}")

        if config.provider == ProviderType.CONFIG:
            if config.key_id:
                params.append(f"KEY_ID '{config.key_id}'")
            if config.secret:
                params.append(f"SECRET '{config.secret}'")
            if config.session_token:
                params.append(f"SESSION_TOKEN '{config.session_token}'")

        if config.region:
            params.append(f"REGION '{config.region}'")
        if config.endpoint:
            params.append(f"ENDPOINT '{config.endpoint}'")

        for k, v in config.extra_params.items():
            params.append(f"{k.upper()} '{v}'")

        param_str = ",\n    ".join(params)
        return f"CREATE {or_replace}{persistent}SECRET {config.name} (\n    {param_str}\n)"

    def drop_secret_sql(self, name: str, if_exists: bool = True) -> str:
        """Generate DROP SECRET SQL.

        Args:
            name: Secret name.
            if_exists: Add IF EXISTS guard.

        Returns:
            DROP SECRET SQL statement.
        """
        guard = "IF EXISTS " if if_exists else ""
        return f"DROP SECRET {guard}{name}"

    def create_all_sql(self) -> list[str]:
        """Return CREATE SECRET SQL for all registered secrets."""
        return [self.create_secret_sql(cfg) for cfg in self._secrets.values()]

    def s3_from_env(self, name: str = "s3_env") -> str:
        """Return SQL for an S3 secret using credential chain (env vars)."""
        cfg = SecretConfig(
            name=name,
            secret_type=SecretType.S3,
            provider=ProviderType.CREDENTIAL_CHAIN,
        )
        return self.create_secret_sql(cfg)
