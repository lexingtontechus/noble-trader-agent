"""
SecretResolver — single entry point for all secret access.

Application code calls `get_secret("alpaca.api_key")` and never knows whether
the value came from .env file, environment variable, HashiCorp Vault, or AWS
Secrets Manager. The backend is a config-time choice via SECRETS_BACKEND env var.

See roadmap §13 for full documentation.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import structlog

log = structlog.get_logger(__name__)


class SecretBackend(Protocol):
    """Protocol all secret backends must implement."""

    def get(self, key: str) -> str:
        """Return the secret value for `key`, or raise SecretNotFoundError."""
        ...


class SecretNotFoundError(KeyError):
    """Raised when a secret cannot be found in any backend."""


class EnvFileBackend:
    """Reads secrets from a .env file via python-dotenv. Default for local dev."""

    def __init__(self, file_path: str | None = None) -> None:
        from dotenv import load_dotenv

        self._file_path = file_path or os.getenv("SECRETS_ENV_FILE_PATH", "./.env")
        if not Path(self._file_path).exists():
            log.warning("env_file_not_found", path=self._file_path)
        # override=True so .env values take precedence over any stale env vars
        load_dotenv(self._file_path, override=True)
        log.info("secret_backend_initialized", backend="env_file", path=self._file_path)

    def get(self, key: str) -> str:
        # python-dotenv loads into os.environ, so we read from there
        # Convert dots to underscores for env var naming convention:
        #   "hermes.duckdb_path" → "HERMES_DUCKDB_PATH" (not "HERMES.DUCKDB_PATH")
        env_key = key.upper().replace(".", "_")
        value = os.getenv(env_key)
        if value is None:
            raise SecretNotFoundError(f"Secret '{key}' not found in .env file (looked for {env_key})")
        return value


class EnvBackend:
    """Reads secrets directly from os.environ. For Docker, CI, serverless."""

    def __init__(self) -> None:
        log.info("secret_backend_initialized", backend="env")

    def get(self, key: str) -> str:
        env_key = key.upper().replace(".", "_")
        value = os.getenv(env_key)
        if value is None:
            raise SecretNotFoundError(f"Secret '{key}' not found in environment (looked for {env_key})")
        return value


class VaultBackend:
    """Fetches secrets from HashiCorp Vault. For production, multi-user."""

    def __init__(self) -> None:
        self._addr = os.getenv("SECRETS_VAULT_ADDR")
        self._token = os.getenv("SECRETS_VAULT_TOKEN")
        if not self._addr or not self._token:
            raise RuntimeError(
                "VaultBackend requires SECRETS_VAULT_ADDR and SECRETS_VAULT_TOKEN"
            )
        # Lazy import — only needed if backend is selected
        try:
            import hvac  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "VaultBackend requires `hvac` package: pip install hvac"
            ) from e
        self._client = hvac.Client(url=self._addr, token=self._token)
        if not self._client.is_authenticated():
            raise RuntimeError("Vault authentication failed — check token")
        self._cache: dict[str, str] = {}
        log.info("secret_backend_initialized", backend="vault", addr=self._addr)

    def get(self, key: str) -> str:
        if key in self._cache:
            return self._cache[key]
        # Convention: secret:alpaca.api_key → mount=secret, path=alpaca, field=api_key
        parts = key.split(".", 1)
        if len(parts) != 2:
            raise SecretNotFoundError(f"Invalid secret key format: '{key}'")
        mount, field = parts
        try:
            response = self._client.secrets.kv.v2.read_secret_version(
                path=mount, mount_point="secret"
            )
            value = response["data"]["data"].get(field)
        except Exception as e:
            raise SecretNotFoundError(f"Vault read failed for '{key}': {e}") from e
        if value is None:
            raise SecretNotFoundError(f"Secret '{key}' not found in Vault")
        self._cache[key] = value
        return value


class AwsSmBackend:
    """Fetches secrets from AWS Secrets Manager. For production on AWS."""

    def __init__(self) -> None:
        region = os.getenv("SECRETS_AWS_REGION", "us-east-1")
        try:
            import boto3  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "AwsSmBackend requires `boto3` package: pip install boto3"
            ) from e
        self._client = boto3.client("secretsmanager", region_name=region)
        self._cache: dict[str, str] = {}
        log.info("secret_backend_initialized", backend="aws_sm", region=region)

    def get(self, key: str) -> str:
        if key in self._cache:
            return self._cache[key]
        try:
            response = self._client.get_secret_value(SecretId=key)
            value = response["SecretString"]
        except Exception as e:
            raise SecretNotFoundError(f"AWS SM read failed for '{key}': {e}") from e
        self._cache[key] = value
        return value


_BACKENDS: dict[str, type] = {
    "env_file": EnvFileBackend,
    "env": EnvBackend,
    "vault": VaultBackend,
    "aws_sm": AwsSmBackend,
}


@lru_cache(maxsize=1)
def _get_backend() -> SecretBackend:
    """Initialize the configured backend (singleton)."""
    backend_name = os.getenv("SECRETS_BACKEND", "env_file")
    backend_cls = _BACKENDS.get(backend_name)
    if backend_cls is None:
        raise RuntimeError(
            f"Unknown SECRETS_BACKEND='{backend_name}'. "
            f"Valid options: {list(_BACKENDS.keys())}"
        )
    return backend_cls()


def get_secret(key: str) -> str:
    """
    Single entry point for secret access.

    Args:
        key: Logical secret name (e.g., "alpaca.api_key", "supabase.url").
             Conventions:
             - env_file/env: uppercased and looked up in os.environ (e.g., ALPACA_API_KEY)
             - vault: split on "." → mount.path, field
             - aws_sm: used as SecretId directly

    Returns:
        The secret value as a string.

    Raises:
        SecretNotFoundError: if the secret cannot be found.
    """
    backend = _get_backend()
    value = backend.get(key)
    log.debug(
        "secret_accessed",
        key=key,
        backend=type(backend).__name__,
        caller=_caller_info(),
        result="success",
    )
    return value


def get_secret_or_none(key: str, default: str | None = None) -> str | None:
    """Like get_secret but returns default instead of raising."""
    try:
        return get_secret(key)
    except SecretNotFoundError:
        return default


def _caller_info() -> str:
    """Return caller's module:function for audit logging."""
    import inspect

    frame = inspect.currentframe()
    if frame is None or frame.f_back is None or frame.f_back.f_back is None:
        return "unknown"
    caller = frame.f_back.f_back
    module = caller.f_globals.get("__name__", "?")
    func = caller.f_code.co_name
    return f"{module}.{func}"


def redact(value: str, visible: int = 4) -> str:
    """Redact a secret for logging. Shows first `visible` chars + '...'."""
    if not value:
        return "<empty>"
    if len(value) <= visible:
        return "*" * len(value)
    return value[:visible] + "..." + f"({len(value)} chars)"
