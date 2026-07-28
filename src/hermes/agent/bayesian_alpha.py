"""Bayesian Alpha — position-sizing modulator from rolling trade outcomes (v5).

NEVER a gate. Always a modulator. alpha ∈ [alpha_floor, 1.0] scales the
position size via:

    final_size = baseline_size * alpha

The alpha is computed from the agent's recent trade outcomes (from the
local DuckDB `pnl_realized` table) using a Beta-Binomial conjugate model:

    Prior:     Beta(2, 2)               — weak, neutral
    Likelihood: Binomial(wins | n, p)   — observed win/loss outcomes
    Posterior:  Beta(2 + wins, 2 + losses)

The posterior mean (2 + wins) / (4 + n) is the alpha. With a strong recent
record (e.g., 8 wins / 2 losses in last 10 trades): alpha = (2+8)/(4+10) = 0.714.
With a poor record (e.g., 2 wins / 8 losses): alpha = (2+2)/(4+10) = 0.286.

A floor (default 0.10) prevents alpha from reaching zero — the agent can
always trade at reduced size, never fully blocked.

A decay weight (default 0.95 per trade) gives exponential recency weighting
to the rolling window so the alpha adapts to regime shifts.

Usage:
    alpha_engine = BayesianAlpha(db_path)
    alpha = alpha_engine.compute_alpha(symbol="BTC")
    # alpha in [0.10, 1.0]

    # On trade close:
    alpha_engine.record_outcome(
        symbol="BTC",
        trade_id="t-123",
        won=True,
        p_win_agent=0.62,
        p_win_server=0.58,
        ev_per_dollar=0.35,
        alpha_at_entry=0.65,
    )
"""

from __future__ import annotations

import math
import time
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Tuple
import threading

logger = logging.getLogger("hermes.bayesian_alpha")


# ── Defaults (overridable via constructor) ───────────────────────────
DEFAULT_ALPHA_FLOOR = 0.10          # Never go below 10% sizing
DEFAULT_ALPHA_CEILING = 1.0         # Never exceed 100% sizing
DEFAULT_PRIOR_ALPHA = 2.0           # Beta prior alpha (weak bullish lean)
DEFAULT_PRIOR_BETA = 2.0            # Beta prior beta
DEFAULT_LOOKBACK_TRADES = 30        # Use last N closed trades for posterior
DEFAULT_DECAY = 0.95                # Exponential recency weight per trade


@dataclass
class AlphaResult:
    """Output of the BayesianAlpha computation."""
    alpha: float                    # The sizing modulator in [floor, ceiling]
    posterior_mean: float           # Raw Beta posterior mean (before floor/ceiling)
    posterior_alpha: float          # Beta posterior alpha param
    posterior_beta: float           # Beta posterior beta param
    n_trades_used: int              # Number of trades in the rolling window
    wins: float                     # Weighted wins
    losses: float                   # Weighted losses
    reason: str = "ok"


class BayesianAlpha:
    """Rolling Bayesian alpha for position-sizing modulation.

    Thread-safe via a single lock around DB reads/writes. The alpha is
    cached per symbol for ``cache_ttl_s`` seconds to avoid hitting DuckDB
    on every sizing call.

    The alpha is NEVER used as a gate. The caller (SizingEngine) multiplies
    the baseline size by alpha. If alpha is at the floor (0.10), the agent
    still trades at 10% of baseline — never fully blocked.
    """

    def __init__(
        self,
        db_path: Path | str,
        alpha_floor: float = DEFAULT_ALPHA_FLOOR,
        alpha_ceiling: float = DEFAULT_ALPHA_CEILING,
        prior_alpha: float = DEFAULT_PRIOR_ALPHA,
        prior_beta: float = DEFAULT_PRIOR_BETA,
        lookback_trades: int = DEFAULT_LOOKBACK_TRADES,
        decay: float = DEFAULT_DECAY,
        cache_ttl_s: float = 30.0,
    ) -> None:
        self._db_path = str(db_path)
        self._alpha_floor = alpha_floor
        self._alpha_ceiling = alpha_ceiling
        self._prior_alpha = prior_alpha
        self._prior_beta = prior_beta
        self._lookback = lookback_trades
        self._decay = decay
        self._cache_ttl_s = cache_ttl_s

        # Per-symbol cache: symbol -> (alpha, timestamp)
        self._cache: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def compute_alpha(self, symbol: str) -> AlphaResult:
        """Compute the Bayesian alpha for a symbol.

        Returns an AlphaResult with the clamped alpha in [floor, ceiling]
        and the raw posterior mean (before clamping) for diagnostic logging.

        On any error (DB missing, table missing, etc.), returns alpha=1.0
        (neutral — no modulation) so the agent can still trade normally.
        """
        # Cache check
        now = time.time()
        cached = self._cache.get(symbol)
        if cached is not None and (now - cached[1]) < self._cache_ttl_s:
            # Return a fresh AlphaResult with the cached alpha — but we
            # don't have the diagnostic fields cached, so re-build them
            # lazily only if needed. For the hot path, just return the
            # cached alpha with placeholder diagnostics.
            return AlphaResult(
                alpha=cached[0],
                posterior_mean=cached[0],
                posterior_alpha=self._prior_alpha,
                posterior_beta=self._prior_beta,
                n_trades_used=0,
                wins=0.0,
                losses=0.0,
                reason="cache_hit",
            )

        try:
            wins, losses, n = self._load_recent_outcomes(symbol)
        except Exception as exc:
            logger.warning(
                f"BayesianAlpha: failed to load outcomes for {symbol} "
                f"({type(exc).__name__}: {exc}) — returning neutral alpha=1.0"
            )
            return AlphaResult(
                alpha=1.0,
                posterior_mean=1.0,
                posterior_alpha=self._prior_alpha,
                posterior_beta=self._prior_beta,
                n_trades_used=0,
                wins=0.0,
                losses=0.0,
                reason=f"error: {type(exc).__name__}",
            )

        # Beta-Binomial conjugate posterior
        # Posterior ~ Beta(prior_alpha + wins, prior_beta + losses)
        post_alpha = self._prior_alpha + wins
        post_beta = self._prior_beta + losses
        posterior_mean = post_alpha / (post_alpha + post_beta)

        # Clamp to [floor, ceiling]
        alpha = max(self._alpha_floor, min(self._alpha_ceiling, posterior_mean))

        # Update cache
        with self._lock:
            self._cache[symbol] = (alpha, now)

        return AlphaResult(
            alpha=alpha,
            posterior_mean=posterior_mean,
            posterior_alpha=post_alpha,
            posterior_beta=post_beta,
            n_trades_used=n,
            wins=wins,
            losses=losses,
            reason="ok",
        )

    def _load_recent_outcomes(self, symbol: str) -> Tuple[float, float, int]:
        """Load weighted wins/losses from the last N closed trades.

        Applies exponential recency weighting (decay=0.95 by default):
        the most recent trade gets weight 1.0, the next-oldest gets 0.95,
        then 0.9025, etc. This makes the alpha adapt to regime shifts.

        Returns (weighted_wins, weighted_losses, n_trades).
        """
        import duckdb

        with duckdb.connect(self._db_path, read_only=True) as conn:
            # Check if the alpha columns exist (migration 014). If not,
            # fall back to plain win/loss counting from net_pnl.
            try:
                cursor = conn.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'pnl_realized'
                      AND column_name = 'p_win_agent'
                    """
                )
                has_alpha_cols = cursor.fetchone() is not None
            except Exception:
                has_alpha_cols = False

            # Load last N trades for this symbol, oldest first (so we can
            # apply decay from most-recent backward)
            rows = conn.execute(
                """
                SELECT net_pnl, p_win_agent, ev_per_dollar
                FROM pnl_realized
                WHERE symbol = ?
                ORDER BY ts DESC
                LIMIT ?
                """,
                [symbol, self._lookback],
            ).fetchall()

        if not rows:
            return (0.0, 0.0, 0)

        # rows are most-recent first. Apply decay so most recent gets
        # weight 1.0, second-most-recent gets decay, etc.
        weighted_wins = 0.0
        weighted_losses = 0.0
        weight = 1.0
        decay_sum = 0.0
        for row in rows:
            net_pnl = float(row[0] or 0.0)
            # Win/loss from net PnL (after fees)
            won = net_pnl > 0.0
            if won:
                weighted_wins += weight
            else:
                weighted_losses += weight
            decay_sum += weight
            weight *= self._decay

        # Normalise so total weight = N (so posterior isn't artificially
        # inflated by the decay weighting)
        if decay_sum > 0:
            scale = len(rows) / decay_sum
            weighted_wins *= scale
            weighted_losses *= scale

        return (weighted_wins, weighted_losses, len(rows))

    def record_outcome(
        self,
        symbol: str,
        trade_id: str,
        won: bool,
        p_win_agent: Optional[float] = None,
        p_win_server: Optional[float] = None,
        ev_per_dollar: Optional[float] = None,
        alpha_at_entry: Optional[float] = None,
    ) -> None:
        """Record a trade outcome for future alpha computation.

        Updates the alpha columns on the existing pnl_realized row for
        this trade_id. If the row doesn't exist or the columns don't
        exist, silently no-ops (the next compute_alpha call will use
        whatever data is available).

        Invalidate the per-symbol cache so the next compute_alpha call
        picks up the new outcome.
        """
        import duckdb

        try:
            with duckdb.connect(self._db_path) as conn:
                # Build SET clause dynamically based on which fields are provided
                sets = []
                params: list = []
                if p_win_agent is not None:
                    sets.append("p_win_agent = ?")
                    params.append(float(p_win_agent))
                if p_win_server is not None:
                    sets.append("p_win_server = ?")
                    params.append(float(p_win_server))
                if ev_per_dollar is not None:
                    sets.append("ev_per_dollar = ?")
                    params.append(float(ev_per_dollar))
                if alpha_at_entry is not None:
                    sets.append("alpha_at_entry = ?")
                    params.append(float(alpha_at_entry))

                if not sets:
                    return  # Nothing to update

                params.append(trade_id)
                sql = f"""
                    UPDATE pnl_realized
                    SET {', '.join(sets)}
                    WHERE trade_id = ?
                """
                conn.execute(sql, params)

            # Invalidate cache for this symbol
            with self._lock:
                self._cache.pop(symbol, None)

        except Exception as exc:
            # Non-fatal — alpha is best-effort. Don't break trade recording.
            logger.warning(
                f"BayesianAlpha: failed to record outcome for {symbol} "
                f"trade {trade_id} ({type(exc).__name__}: {exc})"
            )

    def invalidate_cache(self, symbol: Optional[str] = None) -> None:
        """Force re-computation on next compute_alpha call.

        Pass ``symbol=None`` to invalidate all symbols.
        """
        with self._lock:
            if symbol is None:
                self._cache.clear()
            else:
                self._cache.pop(symbol, None)
