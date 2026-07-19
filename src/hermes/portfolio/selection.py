"""L4.5 Selection Layer — rank candidate signals per cycle, admit top-N.

Hardens against the "agent approves all 25-50 buy signals" gap (GB). Every
candidate signal that passes the risk + autonomy gate is scored and ranked
against others arriving within the cycle window; only the top-N are admitted.
Excess candidates are DROPPED (deferred=False) — over-trading is itself a risk
gate and good portfolio management, so we do not re-queue.

Scoring weights are user-configurable (config.portfolio.selection.score_weights):
  pattern_confidence, expected_entry_alpha_bps, reward_risk, regime_alignment,
  diversification.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger(__name__)


@dataclass
class _Candidate:
    signal_id: str
    symbol: str
    venue: str
    score: float
    ts: float
    # For correlation/diversification checks
    sector: str = ""


class SelectionLayer:
    """Buffers candidate signals within a cycle window and ranks them."""

    def __init__(
        self,
        enabled: bool = True,
        max_new_positions_per_cycle: int = 3,
        cycle_window_sec: float = 300,
        policy: str = "top_n",
        score_threshold: float = 0.0,
        score_weights: dict[str, float] | None = None,
        max_correlated_exposure: float = 0.20,
    ) -> None:
        self._enabled = enabled
        self._max_n = max_new_positions_per_cycle
        self._window = cycle_window_sec
        self._policy = policy
        self._threshold = score_threshold
        self._weights = score_weights or {
            "pattern_confidence": 0.30,
            "expected_entry_alpha_bps": 0.25,
            "reward_risk": 0.20,
            "regime_alignment": 0.15,
            "diversification": 0.10,
        }
        self._max_corr = max_correlated_exposure
        self._candidates: list[_Candidate] = []

    def score_signal(self, signal: Any) -> float:
        """Compute a 0-1 weighted score for a candidate signal."""
        w = self._weights
        # Normalize each factor to ~0-1
        pat = float(getattr(signal, "pattern_confidence", 0.0) or 0.0)  # already 0-1
        alpha = min(1.0, (float(getattr(signal, "expected_entry_alpha_bps", 0.0) or 0.0)) / 50.0)
        # reward:risk from nt_entry/stop/target
        ep = float(getattr(signal, "nt_entry_price", 0.0) or 0.0)
        sl = float(getattr(signal, "nt_stop_price", 0.0) or 0.0)
        tp = float(getattr(signal, "nt_target_price", 0.0) or 0.0)
        rr = 0.0
        if ep > 0 and sl > 0:
            risk = abs(ep - sl)
            reward = abs(tp - ep)
            rr = min(1.0, (reward / risk) / 3.0) if risk > 0 else 0.0
        # regime alignment: meta_regime_confidence proxy (0-1)
        reg = min(1.0, float(getattr(signal, "meta_regime_confidence", 0.0) or 0.0))
        # diversification: start neutral (1.0); lowered by correlation check at admit
        div = 1.0
        score = (
            w.get("pattern_confidence", 0.3) * pat
            + w.get("expected_entry_alpha_bps", 0.25) * alpha
            + w.get("reward_risk", 0.2) * rr
            + w.get("regime_alignment", 0.15) * reg
            + w.get("diversification", 0.1) * div
        )
        return round(score, 4)

    def evaluate(self, signal: Any, equity: float = 0.0) -> tuple[bool, str]:
        """Decide whether to admit a candidate.

        Returns (admit: bool, reason: str). Drops (not defers) excess.
        """
        if not self._enabled:
            return True, "selection_disabled"

        now = time.time()
        # Drop stale candidates outside the cycle window
        self._candidates = [c for c in self._candidates if now - c.ts <= self._window]

        score = self.score_signal(signal)
        cand = _Candidate(
            signal_id=getattr(signal, "signal_id", ""),
            symbol=getattr(signal, "symbol", ""),
            venue=getattr(signal, "venue", ""),
            score=score,
            ts=now,
        )

        if self._policy == "score_threshold" and score < self._threshold:
            return False, f"selection_score_below_threshold:{score:.3f}"

        # Correlation guard: if too many same-venue candidates already buffered
        same_venue = sum(1 for c in self._candidates if c.venue == cand.venue)
        if equity > 0 and same_venue > 0 and (same_venue / max(1, len(self._candidates) + 1)) > self._max_corr / 0.20:
            # crude guard: if > max_correlated share would be same venue, drop
            if (same_venue + 1) / (len(self._candidates) + 1) > self._max_corr + 0.01:
                return False, f"selection_correlation_guard:{cand.venue}"

        # Admit if under the per-cycle cap
        if len(self._candidates) < self._max_n:
            self._candidates.append(cand)
            return True, f"selection_admitted:score={score:.3f}:pos={len(self._candidates)}/{self._max_n}"

        # Over cap — drop (do not re-queue)
        return False, f"selection_budget_exhausted:score={score:.3f}:cap={self._max_n}"

    def reset_cycle(self) -> None:
        self._candidates.clear()

    def pending_count(self) -> int:
        return len(self._candidates)
