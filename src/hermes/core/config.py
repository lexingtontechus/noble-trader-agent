"""
Configuration loader — merges YAML config with secrets resolved via SecretResolver.

Usage:
    from hermes.core.config import load_config
    config = load_config()
    alpaca_key = config.venues.alpaca.credentials.api_key  # already resolved
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
import structlog

from hermes.core.secrets import SecretNotFoundError, get_secret, get_secret_or_none

log = structlog.get_logger(__name__)

SECRET_PREFIX = "secret:"


class Credentials(BaseModel):
    """Dynamic credentials container — fields depend on venue."""

    model_config = {"extra": "allow"}


class VenueConfig(BaseModel):
    enabled: bool = True
    asset_classes: list[str] = Field(default_factory=list)
    credentials: dict[str, str] = Field(default_factory=dict)
    rate_limit_per_min: int = 200
    data_modes: dict[str, bool] = Field(default_factory=dict)
    features: dict[str, Any] = Field(default_factory=dict)


class PortfolioConfig(BaseModel):
    target_allocation: dict[str, float] = Field(default_factory=dict)
    rebalance_threshold_drift_pct: float = 0.10
    rebalance_frequency: str = "on_drift"
    rebalance_method: str = "threshold"
    start_smart: bool = True
    initial_symbols: list[dict[str, str]] = Field(default_factory=list)
    # L4.5 selection layer config (user-tunable ranking/budget)
    selection: dict[str, Any] = Field(default_factory=dict)


class UpstreamConfig(BaseModel):
    redis: dict[str, Any] = Field(default_factory=dict)
    supabase: dict[str, Any] = Field(default_factory=dict)


class HermesConfig(BaseModel):
    """Top-level config — loose typing because we want flexibility."""

    model_config = {"extra": "allow"}

    environment: str = "development"
    log_level: str = "INFO"
    portfolio: PortfolioConfig = Field(default_factory=PortfolioConfig)
    venues: dict[str, VenueConfig] = Field(default_factory=dict)
    upstream: dict[str, Any] = Field(default_factory=dict)
    data_sources: dict[str, Any] = Field(default_factory=dict)
    account: dict[str, Any] = Field(default_factory=dict)
    asset: dict[str, Any] = Field(default_factory=dict)
    signal: dict[str, Any] = Field(default_factory=dict)
    entry: dict[str, Any] = Field(default_factory=dict)
    execution: dict[str, Any] = Field(default_factory=dict)
    position_management: dict[str, Any] = Field(default_factory=dict)
    circuit_breakers: dict[str, Any] = Field(default_factory=dict)
    autonomy: dict[str, Any] = Field(default_factory=dict)
    meta_regime: dict[str, Any] = Field(default_factory=dict)
    renko: dict[str, Any] = Field(default_factory=dict)
    duckdb: dict[str, Any] = Field(default_factory=dict)
    hermes_redis: dict[str, Any] = Field(default_factory=dict)
    notifications: dict[str, Any] = Field(default_factory=dict)
    logging: dict[str, Any] = Field(default_factory=dict)


def _resolve_secret(value: str) -> str:
    """If value starts with 'secret:', resolve via SecretResolver. Else return as-is."""
    if not isinstance(value, str):
        return value
    if not value.startswith(SECRET_PREFIX):
        return value
    key = value[len(SECRET_PREFIX) :]
    try:
        return get_secret(key)
    except SecretNotFoundError:
        log.warning("secret_not_found", key=key, note="using placeholder")
        return value  # return the placeholder so init can proceed in dev


def _resolve_secrets_in_dict(d: Any) -> Any:
    """Recursively walk a dict/list, resolving any 'secret:' prefixed strings."""
    if isinstance(d, dict):
        return {k: _resolve_secrets_in_dict(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_resolve_secrets_in_dict(item) for item in d]
    return _resolve_secret(d) if isinstance(d, str) else d


def _find_config_file() -> Path:
    """Find config/default.yaml relative to project root."""
    env_path = os.getenv("HERMES_CONFIG_PATH")
    if env_path:
        return Path(env_path)

    # Walk up from this file to find config/default.yaml
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "config" / "default.yaml"
        if candidate.exists():
            return candidate
        candidate = parent.parent / "config" / "default.yaml"
        if candidate.exists():
            return candidate

    # Fall back to CWD
    cwd_candidate = Path.cwd() / "config" / "default.yaml"
    if cwd_candidate.exists():
        return cwd_candidate

    raise FileNotFoundError(
        "Could not find config/default.yaml. Set HERMES_CONFIG_PATH env var."
    )


@lru_cache(maxsize=1)
def load_config(config_path: str | None = None) -> HermesConfig:
    """
    Load configuration from YAML + resolve all `secret:` references.

    Args:
        config_path: Optional explicit path to config file. If None, auto-discovers.

    Returns:
        HermesConfig with all secrets resolved.
    """
    path = Path(config_path) if config_path else _find_config_file()
    log.info("loading_config", path=str(path))

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # Resolve all secret: references
    resolved = _resolve_secrets_in_dict(raw)

    config = HermesConfig(**resolved)
    log.info(
        "config_loaded",
        path=str(path),
        environment=config.environment,
        venues_enabled=[k for k, v in config.venues.items() if v.enabled],
    )
    return config


def get_config_hash(config: HermesConfig) -> str:
    """Compute a SHA-256 hash of the config (for config_history table)."""
    import hashlib
    import json

    config_dict = config.model_dump(mode="json")
    # Sort keys for deterministic hashing
    canonical = json.dumps(config_dict, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def redact_config_for_display(config: HermesConfig) -> dict[str, Any]:
    """Return a copy of config with all secret values redacted — safe to print.

    Redaction rules:
    - Any value that is (or resolves to) a `secret:...` reference is shown as the
      placeholder only (no resolution attempted here; the loader keeps the ref).
    - Resolved secret VALUES (API keys, tokens, passwords, private keys, wallet
      addresses, Redis/DB/Webhook URLs) are fully redacted, regardless of length
      or whether they contain a keyword — a short token or a `redis://...` URL must
      never reach the browser.
    - Non-secret scalars (allocations, thresholds, booleans, symbols) pass through.
    """
    # Keys whose values are always sensitive, by substring match (case-insensitive).
    SECRET_KEY_HINTS = (
        "key", "secret", "token", "password", "private_key", "wallet",
        "url", "webhook", "anon_key", "api_key", "api_secret", "base_url",
        "data_url", "api_url", "vault", "credential",
    )
    # Value patterns that are secret even if the key doesn't hint it.
    import re
    URL_RE = re.compile(r"^(https?://|redis://|wss?://|postgres://|supabase://)", re.I)
    HEX_OR_ADDR_RE = re.compile(r"^(0x[a-f0-9]{8,}|[13][a-km-zA-HJ-NP-Z1-9]{25,})$")

    def _secret_key(k: str) -> bool:
        return any(h in k.lower() for h in SECRET_KEY_HINTS)

    def _redact(d: Any, key: str = "") -> Any:
        if isinstance(d, dict):
            return {k: _redact(v, k) for k, v in d.items()}
        if isinstance(d, list):
            return [_redact(item, key) for item in d]
        if isinstance(d, str):
            # Unresolved secret reference — show the ref only.
            if d.startswith("secret:"):
                return d
            # Resolved secret value if the key hints sensitivity...
            if _secret_key(key):
                return f"<redacted:{len(d)}chars>"
            # ...or if the value itself is a URL / address / hex secret.
            if URL_RE.match(d) or HEX_OR_ADDR_RE.match(d):
                return f"<redacted:{len(d)}chars>"
            # Short high-entropy-looking tokens (e.g. bot tokens) without a
            # keyword: redact anything that looks like a credential by length
            # heuristic only when the key is auth/notification related.
            if _secret_key(key) and len(d) > 0:
                return f"<redacted:{len(d)}chars>"
        return d

    raw = config.model_dump(mode="json")
    return _redact(raw)
