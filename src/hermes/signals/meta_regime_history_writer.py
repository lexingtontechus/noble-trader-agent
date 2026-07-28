"""Non-blocking writer for the `meta_regime_history` DuckDB table.

Background
----------
The table `meta_regime_history` is declared in `db/schema.sql:245` but never
written to. As a result, agent_ops.py:525 (`SELECT 1 FROM meta_regime_history
LIMIT 1`) returns "table not found" until the first write lands, and the
meta-regime radial chart (`web/charts/regime_probs.py`) falls back to running
`MetaRegimeClassifier` live on each request.

This module adds a minimal state-transition writer:
  - Called from `SignalSynthesizer.process_heartbeat` AFTER
    `MetaRegimeClassifier.classify()` produces a result.
  - Compares the new state to the per-symbol previously-seen state.
  - If they differ (or this is the first classification for the symbol),
    inserts a row into `meta_regime_history` with trigger='shift' (or
    'initial' for the first observation).
  - Fire-and-forget: failures are logged at warning level and swallowed
    so they never break the signal-processing hot path.

What's NOT here (deferred)
--------------------------
  - PnL-correlation fields (`pnl_5m_after`, `pnl_15m_after`, `pnl_1h_after`,
    `correct_call`) are written as NULL. Backfilling them requires a
    scheduled post-trade tracking task — out of scope for this fix.
  - `transition_probs` is NULL — `MetaRegimeClassifier` is rule-based and
    does not produce a transition matrix.
  - Per-asset vs portfolio scope distinction — we always write scope='asset'
    because the classifier runs per-symbol here.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import duckdb
from structlog import get_logger

log = get_logger(__name__)


def _to_jsonable(value: Any) -> Any:
    """Coerce arbitrary dict/list/scalar into JSON-serialisable form for DuckDB."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    # Pydantic BaseModel / Enum / anything else → stringify
    return str(value)


def record_meta_regime_transition(
    db_path,
    symbol: str,
    prev_state: str | None,
    new_state: str,
    confidence: float,
    posterior_probs: dict[str, Any] | None,
    upstream_regime: str | None = None,
    upstream_regime_conf: float | None = None,
    trigger: str = "shift",
    trigger_detail: dict | None = None,
    extra_cols: dict | None = None,
) -> bool:
    """Insert one row into meta_regime_history. Returns True on success.

    All exceptions are caught + logged — this is a fire-and-forget path
    and MUST NOT raise into the caller (the signal-processing hot path).
    """
    try:
        if db_path is None:
            return False
        import pathlib

        path = pathlib.Path(db_path) if not isinstance(db_path, pathlib.Path) else db_path
        if not path.exists():
            return False

        event_id = str(uuid.uuid4())
        ts = datetime.now(timezone.utc)
        posterior_json = json.dumps(_to_jsonable(posterior_probs or {}))
        trigger_detail_json = json.dumps(_to_jsonable(trigger_detail or {}))

        # Pull optional extras (e.g. cross_asset_corr_mean, funding_rate_8h,
        # book_depth_percentile) — written only if the caller supplied them.
        extras = extra_cols or {}

        with duckdb.connect(str(path)) as conn:
            conn.execute(
                """
                INSERT INTO meta_regime_history (
                    event_id, ts, symbol, scope,
                    prev_state, new_state, confidence,
                    posterior_probs, transition_probs,
                    upstream_regime, upstream_regime_conf,
                    cross_asset_corr_mean, funding_rate_8h,
                    book_depth_percentile, spread_percentile,
                    posterior_entropy,
                    trigger, trigger_detail,
                    pnl_5m_after, pnl_15m_after, pnl_1h_after, correct_call
                ) VALUES (
                    ?, ?, ?, 'asset',
                    ?, ?, ?,
                    ?, NULL,
                    ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?,
                    NULL, NULL, NULL, NULL
                )
                """,
                [
                    event_id, ts, symbol,
                    prev_state, new_state, float(confidence),
                    posterior_json,
                    upstream_regime, upstream_regime_conf,
                    extras.get("cross_asset_corr_mean"),
                    extras.get("funding_rate_8h"),
                    extras.get("book_depth_percentile"),
                    extras.get("spread_percentile"),
                    extras.get("posterior_entropy"),
                    trigger, trigger_detail_json,
                ],
            )
        return True
    except Exception as exc:  # noqa: BLE001 — fire-and-forget
        log.warning(
            "meta_regime_history_write_failed",
            symbol=symbol,
            prev_state=prev_state,
            new_state=new_state,
            error=str(exc)[:200],
        )
        return False
