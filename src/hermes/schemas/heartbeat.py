"""
Noble Trader heartbeat schema (Pydantic v2).

Validates every field from Noble Trader's heartbeat payload.
See roadmap §5.1 for the full schema reference.

The heartbeat carries the same fields whether it's a true actionable signal
or a keep-alive (the `signal` field will be "neutral" for keep-alives).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class NobleTraderHeartbeat(BaseModel):
    """
    Validated Noble Trader heartbeat.

    All fields come from the upstream Redis channel. Hermes assigns its own
    `heartbeat_id` (UUID) on ingest — the upstream `ts` is preserved as
    `ts_upstream`.
    """

    model_config = {"extra": "allow"}  # accept unknown fields for forward compat

    # === Identity ===
    type: Literal["heartbeat"] = "heartbeat"
    symbol: str = Field(..., min_length=1, description="Trading symbol, e.g. 'BTC', 'BTC/USD', 'SOL/USD'")
    ts: int = Field(..., description="Unix ms timestamp from upstream")

    # === Upstream signal ===
    signal: Literal["buy", "sell", "neutral"] = Field(..., description="Trade direction")
    entry_price: float = Field(..., gt=0, description="Suggested entry price")
    stop_loss: float = Field(..., gt=0, description="Stop-loss price")
    take_profit: float = Field(..., gt=0, description="Take-profit price")
    aggression: Literal["passive", "mid", "aggressive"] = Field(
        ..., description="Routing hint"
    )

    # === Renko ===
    brick_size: float = Field(..., gt=0, description="Renko brick size used upstream")
    sl_bricks: float = Field(..., gt=0, description="Stop distance in bricks")
    tp_bricks: float = Field(..., gt=0, description="Target distance in bricks")

    # === Upstream regime (Noble Trader's per-asset 4×4 HMM) ===
    regime: str = Field(..., description="Composite regime label, e.g. 'low_vol_bull'")
    regime_conf: float = Field(..., ge=0, le=1, description="HMM posterior confidence")
    regime_shift: Literal["true", "false"] = Field(
        ..., description="Did regime change this cycle?"
    )
    prev_regime: str | None = Field(None, description="Previous regime label before shift")
    shift_at: int = Field(..., ge=0, description="Unix ms when shift was detected")
    shifts_24h: int = Field(..., ge=0, description="Regime shifts in last 24h")

    # === Upstream EV engine v4 ===
    kelly_f: float = Field(..., ge=0, description="Base Kelly fraction (full-Kelly, pre-cap)")
    effective_kelly: float = Field(..., ge=0, description="Capped Kelly actually used upstream")
    ev: float = Field(..., description="Expected value")
    ev_per_dollar: float = Field(..., description="EV normalized per dollar risked")
    p_win: float = Field(..., ge=0, le=1, description="EV Engine v4 blended P_win")
    p_regime: float = Field(..., ge=0, le=1, description="HMM regime component of P_win")
    p_imbalance: float = Field(..., ge=0, le=1, description="L2 imbalance component")
    p_markov: float = Field(..., ge=0, le=1, description="Markov transition component")
    ev_scale: float = Field(..., description="EV-scaled Kelly multiplier")

    # === TimesFM (optional — null if unavailable) ===
    p_timesfm: float | None = Field(
        None, ge=0, le=1, description="TimesFM directional forecast (0-1)"
    )
    timesfm_horizon: str | None = Field(None, description="Forecast window label, e.g. '12h'")

    # === Markov ===
    markov_current_state: Literal["UP", "DOWN", "FLAT"] = Field(
        ..., description="Current Markov state"
    )

    # === Tail risk (optional) ===
    tail_risk_score: float | None = Field(
        None, ge=0, le=1, description="0=none, 0.35=mild, 0.60=moderate, 0.85=critical"
    )
    tail_risk_action: Literal["none", "reduce_25", "reduce_50", "skip"] | None = Field(
        None, description="Recommended action"
    )

    # === Hermes-assigned (added on ingest, not from upstream) ===
    heartbeat_id: str | None = Field(None, description="UUID assigned by Hermes L0")
    strategy_id: str | None = Field(None, description="Inferred from Redis channel name")

    @field_validator("ts", "shift_at", mode="before")
    @classmethod
    def _coerce_int(cls, v: object) -> int:
        """Coerce stringified numbers to int (Redis sends everything as bytes)."""
        if v is None:
            return 0
        return int(v)

    @field_validator(
        "entry_price",
        "stop_loss",
        "take_profit",
        "brick_size",
        "sl_bricks",
        "tp_bricks",
        "regime_conf",
        "kelly_f",
        "effective_kelly",
        "ev",
        "ev_per_dollar",
        "p_win",
        "p_regime",
        "p_imbalance",
        "p_markov",
        "ev_scale",
        "p_timesfm",
        "tail_risk_score",
        mode="before",
    )
    @classmethod
    def _coerce_float(cls, v: object) -> float | None:
        """Coerce stringified numbers to float."""
        if v is None:
            return None
        return float(v)

    @model_validator(mode="after")
    def _validate_regime_shift_consistency(self) -> NobleTraderHeartbeat:
        """If regime_shift is 'true', prev_regime should be present."""
        if self.regime_shift == "true" and self.prev_regime is None:
            # Not a hard error — log it but don't reject
            pass
        return self

    def to_duckdb_row(
        self, ts_received: datetime, dedup_hash: str, accepted: bool = True,
        reject_reason: str | None = None, raw_payload: str = "",
    ) -> dict:
        """
        Convert to a dict ready for INSERT into signal_heartbeats table.

        Args:
            ts_received: When Hermes L0 received this heartbeat
            dedup_hash: SHA-256 hash for dedup
            accepted: Whether L0 accepted the heartbeat
            reject_reason: If rejected, why
            raw_payload: Original JSON string for audit
        """
        ts_upstream = datetime.fromtimestamp(self.ts / 1000, tz=timezone.utc)
        lag_ms = int((ts_received - ts_upstream).total_seconds() * 1000)

        shift_at_dt = None
        if self.shift_at > 0:
            shift_at_dt = datetime.fromtimestamp(self.shift_at / 1000, tz=timezone.utc)

        return {
            "heartbeat_id": self.heartbeat_id or str(uuid4()),
            "ts_received": ts_received,
            "ts_upstream": ts_upstream,
            "lag_ms": lag_ms,
            "dedup_hash": dedup_hash,
            "symbol": self.symbol,
            "strategy_id": self.strategy_id or "unknown",
            "type": self.type,
            "signal": self.signal,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "aggression": self.aggression,
            "brick_size": self.brick_size,
            "sl_bricks": self.sl_bricks,
            "tp_bricks": self.tp_bricks,
            "regime": self.regime,
            "regime_conf": self.regime_conf,
            "regime_shift": self.regime_shift == "true",
            "prev_regime": self.prev_regime,
            "shift_at": shift_at_dt,
            "shifts_24h": self.shifts_24h,
            "ev": self.ev,
            "ev_per_dollar": self.ev_per_dollar,
            "p_win": self.p_win,
            "p_regime": self.p_regime,
            "p_imbalance": self.p_imbalance,
            "p_markov": self.p_markov,
            "ev_scale": self.ev_scale,
            "p_timesfm": self.p_timesfm,
            "timesfm_horizon": self.timesfm_horizon,
            "markov_current_state": self.markov_current_state,
            "tail_risk_score": self.tail_risk_score,
            "tail_risk_action": self.tail_risk_action,
            "kelly_f": self.kelly_f,
            "effective_kelly": self.effective_kelly,
            "raw_payload": raw_payload,
            "accepted": accepted,
            "reject_reason": reject_reason,
            "reprocessed_at": None,
        }


class HeartbeatValidationError(Exception):
    """Raised when a heartbeat fails Pydantic validation."""

    def __init__(self, message: str, errors: list[dict] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


def parse_heartbeat(
    payload: bytes | str, strategy_id: str | None = None
) -> NobleTraderHeartbeat:
    """
    Parse and validate a Noble Trader heartbeat from raw Redis payload.

    Args:
        payload: Raw bytes or string from Redis (JSON)
        strategy_id: Optional strategy ID (inferred from channel name by caller)

    Returns:
        Validated NobleTraderHeartbeat

    Raises:
        HeartbeatValidationError: If payload is not valid JSON or fails schema validation
    """
    import json

    if isinstance(payload, bytes):
        payload_str = payload.decode("utf-8")
    else:
        payload_str = payload

    try:
        data = json.loads(payload_str)
    except json.JSONDecodeError as e:
        raise HeartbeatValidationError(
            f"Invalid JSON: {e}", errors=[{"loc": ["json"], "msg": str(e)}]
        ) from e

    try:
        hb = NobleTraderHeartbeat(**data)
        if strategy_id:
            hb.strategy_id = strategy_id
        return hb
    except Exception as e:
        # Extract Pydantic errors if available
        errors = []
        if hasattr(e, "errors"):
            try:
                errors = e.errors()  # type: ignore
            except Exception:
                pass
        raise HeartbeatValidationError(
            f"Schema validation failed: {e}", errors=errors
        ) from e
