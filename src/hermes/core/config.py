"""Configuration loader — merges YAML config with secrets resolved via SecretResolver.

Usage:
    from hermes.core.config import load_config
    config = load_config()
    alpaca_key = config.venues.alpaca.credentials.api_key  # already resolved

Configuration Loading Order (later files override earlier):
    1. config/system_endpoints.yaml (system URLs, infrastructure)
    2. config/user.local.yaml (user overrides, git-ignored)
    3. config/default.yaml (behavioral defaults)
    4. Secret resolution from .env, Vault, AWS SM, etc.
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
    
    model_config = {"extra": "allow"}  # Allows api_url, api_host, etc. at venue level


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


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dictionaries, with override taking precedence.
    
    Unlike a simple update(), this recursively merges nested dictionaries
    instead of replacing them entirely.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _find_config_file(name: str, search_dirs: list[Path] | None = None) -> Path | None:
    """Find a config file by name, searching from project root or specified dirs.
    
    Args:
        name: Config file name (e.g., 'default.yaml', 'system_endpoints.yaml')
        search_dirs: Optional list of directories to search. If None, auto-discovers.
    
    Returns:
        Path to the config file, or None if not found.
    """
    if search_dirs is None:
        # Walk up from this file to find config directory
        here = Path(__file__).resolve()
        search_dirs = []
        for parent in [here.parent, *here.parents]:
            candidate = parent / "config"
            if candidate.exists() and candidate.is_dir():
                search_dirs = [candidate]
                break
    
    for config_dir in search_dirs:
        candidate = config_dir / name
        if candidate.exists():
            return candidate
    
    # Fall back to CWD
    cwd_candidate = Path.cwd() / "config" / name
    if cwd_candidate.exists():
        return cwd_candidate
    
    return None


def _load_yaml_config(path: Path) -> dict[str, Any]:
    """Load a YAML config file and return as dictionary."""
    if not path.exists():
        log.debug("config_file_not_found", path=str(path))
        return {}
    
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    
    log.debug("config_file_loaded", path=str(path))
    return raw


def _find_project_root() -> Path:
    """Find the project root directory (containing config/ and src/)."""
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "config").exists() and (parent / "src").exists():
            return parent
    return Path.cwd()


def load_config(config_path: str | None = None) -> HermesConfig:
    """
    Load configuration from multiple YAML files + resolve all `secret:` references.

    Loading order (later files override earlier):
        1. config/system_endpoints.yaml (system URLs, infrastructure)
        2. config/user.local.yaml (user overrides, git-ignored, if exists)
        3. config/default.yaml (behavioral defaults)
        4. Secret resolution from .env, Vault, AWS SM, etc.

    Args:
        config_path: Optional explicit path to config file. If None, auto-discovers.
                     Only used for backward compatibility.

    Returns:
        HermesConfig with all secrets resolved.
    """
    project_root = _find_project_root()
    config_dir = project_root / "config"
    
    # Build config in layers
    merged_config: dict[str, Any] = {}
    
    # Layer 1: system_endpoints.yaml (system URLs, infrastructure)
    system_endpoints_path = config_dir / "system_endpoints.yaml"
    if system_endpoints_path.exists():
        system_config = _load_yaml_config(system_endpoints_path)
        merged_config = _deep_merge(merged_config, system_config)
        log.info("system_endpoints_loaded", path=str(system_endpoints_path))
    else:
        log.warning("system_endpoints_not_found", path=str(system_endpoints_path))
    
    # Layer 2: user.local.yaml (user overrides, git-ignored)
    user_local_path = config_dir / "user.local.yaml"
    if user_local_path.exists():
        user_config = _load_yaml_config(user_local_path)
        merged_config = _deep_merge(merged_config, user_config)
        log.info("user_config_loaded", path=str(user_local_path))
    
    # Layer 3: default.yaml (behavioral defaults)
    # Use provided path or auto-discover
    if config_path:
        default_path = Path(config_path)
    else:
        default_path = config_dir / "default.yaml"
        if not default_path.exists():
            # Legacy support: HERMES_CONFIG_PATH env var
            env_path = os.getenv("HERMES_CONFIG_PATH")
            if env_path:
                default_path = Path(env_path)
    
    if default_path.exists():
        default_config = _load_yaml_config(default_path)
        merged_config = _deep_merge(merged_config, default_config)
        log.info("default_config_loaded", path=str(default_path))
    else:
        log.warning("default_config_not_found", path=str(default_path))
    
    # Resolve all secret: references
    resolved = _resolve_secrets_in_dict(merged_config)
    
    config = HermesConfig(**resolved)
    log.info(
        "config_loaded",
        path=str(default_path),
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