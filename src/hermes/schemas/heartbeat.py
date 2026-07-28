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


def _json_dumps_or_none(v: object) -> str | None:
    """JSON-encode a list/dict for DuckDB VARCHAR storage, or None if missing."""
    if v is None:
        return None
    import json as _json
    try:
        return _json.dumps(v, default=str)
    except (TypeError, ValueError):
        return None


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
    # Made Optional with default=0 (audit 2026-07-22): the backend's agent_payload
    # (orchestrator.py:1521-1576 + heavy_sweep.py:755-791) does NOT include these
    # fields. Requiring them caused every live heartbeat to fail Pydantic
    # validation and be quarantined. Default values are conservative.
    shift_at: int = Field(0, ge=0, description="Unix ms when shift was detected (0 if not sent)")
    shifts_24h: int = Field(0, ge=0, description="Regime shifts in last 24h (0 if not sent)")

    # === Upstream EV engine v4 ===
    kelly_f: float = Field(..., ge=0, description="Base Kelly fraction (full-Kelly, pre-cap)")
    effective_kelly: float = Field(..., ge=0, description="Capped Kelly actually used upstream")
    ev: float = Field(..., description="Expected value")
    ev_per_dollar: float = Field(..., description="EV normalized per dollar risked")
    p_win: float = Field(..., ge=0, le=1, description="EV Engine v4 blended P_win")
    p_regime: float = Field(..., ge=0, le=1, description="HMM regime component of P_win")
    p_markov: float = Field(..., ge=0, le=1, description="Markov transition component")
    # Made Optional with default=0.5 (audit 2026-07-22): L2 imbalance was
    # deprecated in P3.5; the backend no longer sends p_imbalance. Default
    # to 0.5 (no information) instead of requiring it.
    p_imbalance: float = Field(0.5, ge=0, le=1, description="L2 imbalance component (deprecated P3.5, defaults to 0.5)")
    ev_scale: float = Field(..., description="EV-scaled Kelly multiplier")

    # === TimesFM (optional — null if unavailable) ===
    p_timesfm: float | None = Field(
        None, ge=0, le=1, description="TimesFM directional forecast (0-1)"
    )
    timesfm_horizon: str | None = Field(None, description="Forecast window label, e.g. '12h'")

    # === Markov ===
    # Made Optional with default='FLAT' (audit 2026-07-22): the backend's
    # agent_payload does not include this field. Defaulting to FLAT is
    # conservative (neutral).
    markov_current_state: Literal["UP", "DOWN", "FLAT"] = Field(
        "FLAT", description="Current Markov state (defaults to FLAT if not sent)"
    )
    # ── HIGH #8 (2026-07-23): single-step Markov T ──────────────────
    # Backend now sends both the single-step transition probability and
    # the full 3x3 transition matrix. The agent's synthesizer uses
    # p_markov_single_step as `markov_persistence` for the decision
    # tree's adaptive-threshold check (markov_persistence > 0.7 ⇒
    # "let winners run" branch in trending regimes). Previously the
    # synthesizer used heartbeat.p_markov (the T^N multi-step hold
    # probability) as a proxy, which was conservative-but-wrong in
    # mean-reverting regimes.
    #
    # Default to 0.5 (neutral) for backtest replay of pre-HIGH-#8
    # heartbeats that don't have these fields.
    p_markov_single_step: float = Field(
        0.5, ge=0, le=1,
        description="Single-step T[current_state][target_state] probability. "
                    "target = 'UP' for long, 'DOWN' for short. "
                    "Defaults to 0.5 (neutral) for pre-HIGH-#8 heartbeats."
    )
    markov_transition_matrix: dict[str, dict[str, float]] | None = Field(
        None,
        description="3x3 Markov transition matrix keyed by {UP, DOWN, FLAT}. "
                    "Sent by backend (HIGH #8); None for pre-HIGH-#8 heartbeats."
    )

    # === Tail risk (optional) ===
    tail_risk_score: float | None = Field(
        None, ge=0, le=1, description="0=none, 0.35=mild, 0.60=moderate, 0.85=critical"
    )
    tail_risk_action: Literal["none", "reduce_25", "reduce_50", "skip"] | None = Field(
        None, description="Recommended action"
    )

    # === v5 EV source breakdown (Phase C — backend already sends these) ===
    # The backend runs the 4-source logit-pool blend in EVEngine.compute_ev
    # (ev_engine.py:408-539) and pushes the full per-source breakdown in
    # every agent_payload (orchestrator.py:1547-1556). The agent RE-BLENDS
    # these locally via compute_blended_p_win in synthesizer.py, overriding
    # p_pattern with its own Wilson-confident pattern_performance value
    # (the one source the server cannot know).
    p_pattern: float | None = Field(
        None, ge=0, le=1,
        description="Server-side P_pattern (always 0.5 — server has no trade "
                    "journal). Agent overrides with local pattern_performance "
                    "Wilson lower bound."
    )
    sources_used: list[str] | None = Field(
        None, description="Which of the 4 sources the backend actually had "
                          "available (e.g. ['p_regime','p_pattern','p_markov'] "
                          "when TimesFM was unreachable). Agent must drop the "
                          "same source to avoid double-counting."
    )
    weights_used: dict[str, float] | None = Field(
        None, description="Backend's renormalised weights over the available "
                          "sources. Agent uses these (re-normalised again after "
                          "overriding p_pattern) instead of defaults."
    )
    calibration_bias: float | None = Field(
        None, ge=-1, le=1,
        description="Server-side P_win bias = avg_predicted_p_win - actual_win_rate. "
                    "Positive = overconfident; agent should shrink p_regime toward 0.5."
    )
    calibration_status: str | None = Field(
        None, description="CALIBRATED | OVERCONFIDENT | UNDERCONFIDENT | "
                          "INSUFFICIENT_DATA"
    )
    p_win_kelly_shrink: float | None = Field(
        None, ge=0, le=1,
        description="Server-side p_win soft-gate Kelly shrink factor. "
                    "1.0 = full kelly (p_win >= MIN_P_WIN). <1.0 = linearly "
                    "shrunk in the soft band (0.50 <= p_win < MIN_P_WIN). "
                    "0.0 = hard floor (p_win < 0.50, not published anyway)."
    )

    # === Hermes-assigned (added on ingest, not from upstream) ===
    heartbeat_id: str | None = Field(None, description="UUID assigned by Hermes L0")
    strategy_id: str | None = Field(None, description="Inferred from Redis channel name")
    # Multi-tenant / multi-source attribution. Set by the bridge gateway (see
    # bridges/mt4_mt5/bridge_relay.py) so sources sharing one stream stay
    # distinguishable in signal_heartbeats. Optional; null for legacy NT pushes.
    source_id: str | None = Field(
        None, description="Publisher identity (tenant/agent/source). Stamped by bridge gateway."
    )

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
        "p_pattern",
        "calibration_bias",
        "p_win_kelly_shrink",
        "p_markov_single_step",
        mode="before",
    )
    @classmethod
    def _coerce_float(cls, v: object) -> float | None:
        """Coerce stringified numbers to float."""
        if v is None:
            return None
        return float(v)

    @field_validator("markov_transition_matrix", mode="before")
    @classmethod
    def _coerce_transition_matrix(cls, v: object) -> dict[str, dict[str, float]] | None:
        """Coerce JSON-stringified transition matrix (Redis sends bytes).

        Backend sends a 3x3 dict-of-dicts keyed by {UP, DOWN, FLAT}. If
        a stale payload arrives with the field as a JSON string (older
        codec path), decode it here.
        """
        if v is None:
            return None
        if isinstance(v, dict):
            try:
                return {
                    str(k): {str(k2): float(v2) for k2, v2 in row.items()}
                    for k, row in v.items()
                }
            except (TypeError, ValueError):
                return None
        if isinstance(v, str):
            import json as _json
            try:
                decoded = _json.loads(v)
                if isinstance(decoded, dict):
                    return {
                        str(k): {str(k2): float(v2) for k2, v2 in row.items()}
                        for k, row in decoded.items()
                    }
            except (ValueError, TypeError):
                return None
        return None

    @field_validator("sources_used", mode="before")
    @classmethod
    def _coerce_sources_used(cls, v: object) -> list[str] | None:
        """Coerce JSON-stringified list (Redis sends bytes) to list[str].

        Backend sends sources_used as a JSON array in the agent_payload;
        after json.loads() at parse_heartbeat() it's already a list. But
        if a stale payload arrives with the field as a JSON string (older
        codec path), decode it here.
        """
        if v is None:
            return None
        if isinstance(v, list):
            return [str(s) for s in v]
        if isinstance(v, str):
            import json as _json
            try:
                decoded = _json.loads(v)
                if isinstance(decoded, list):
                    return [str(s) for s in decoded]
            except (ValueError, TypeError):
                return None
        return None

    @field_validator("weights_used", mode="before")
    @classmethod
    def _coerce_weights_used(cls, v: object) -> dict[str, float] | None:
        """Coerce JSON-stringified dict (Redis sends bytes) to dict[str, float]."""
        if v is None:
            return None
        if isinstance(v, dict):
            return {str(k): float(val) for k, val in v.items()}
        if isinstance(v, str):
            import json as _json
            try:
                decoded = _json.loads(v)
                if isinstance(decoded, dict):
                    return {str(k): float(val) for k, val in decoded.items()}
            except (ValueError, TypeError):
                return None
        return None

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
            # ── v5 EV source breakdown (Phase C) ───────────────────────
            "p_pattern": self.p_pattern,
            "sources_used": _json_dumps_or_none(self.sources_used),
            "weights_used": _json_dumps_or_none(self.weights_used),
            "calibration_bias": self.calibration_bias,
            "calibration_status": self.calibration_status,
            "p_win_kelly_shrink": self.p_win_kelly_shrink,
            # ── HIGH #8 (2026-07-23): single-step Markov T ──────────────
            "p_markov_single_step": self.p_markov_single_step,
            "markov_transition_matrix": _json_dumps_or_none(self.markov_transition_matrix),
            # ───────────────────────────────────────────────────────────
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
