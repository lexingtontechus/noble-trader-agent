"""
VaR / CVaR calculator — historical + parametric.

Value at Risk: the maximum expected loss over a given time horizon
at a given confidence level.

CVaR (Expected Shortfall): the average loss given that the loss exceeds VaR.

See roadmap §4.2.
"""

from __future__ import annotations

import math
from collections import deque
from datetime import datetime, timezone

import numpy as np
import structlog

log = structlog.get_logger(__name__)


class VaRCalculator:
    """
    Computes Value at Risk and Conditional VaR (Expected Shortfall).

    Two methods:
    1. Historical (empirical) — uses actual return distribution, robust to fat tails
    2. Parametric (Gaussian) — assumes normal distribution, fast but underestimates tail risk

    Usage:
        calc = VaRCalculator()
        calc.add_return(0.001)  # daily return
        var, cvar = calc.compute(var_confidence=0.99)
    """

    def __init__(self, max_returns: int = 500) -> None:
        self._returns: deque[float] = deque(maxlen=max_returns)
        self._stats = {"computations": 0}

    def add_return(self, daily_return: float) -> None:
        """Add a daily portfolio return."""
        self._returns.append(daily_return)

    def add_returns(self, returns: list[float]) -> None:
        """Add multiple daily returns."""
        for r in returns:
            self._returns.append(r)

    def compute_historical(
        self,
        confidence: float = 0.99,
        position_value: float | None = None,
    ) -> tuple[float, float]:
        """
        Historical VaR + CVaR.

        Args:
            confidence: Confidence level (0.99 = 99% VaR)
            position_value: If provided, return $ amounts; else return as decimals

        Returns:
            (var, cvar) as decimals or USD amounts
        """
        self._stats["computations"] += 1
        if len(self._returns) < 30:
            return 0.0, 0.0

        returns = np.array(list(self._returns))
        percentile = (1 - confidence) * 100  # e.g., 99% → 1st percentile

        var = float(np.percentile(returns, percentile))
        # CVaR = mean of returns below VaR
        tail = returns[returns <= var]
        cvar = float(np.mean(tail)) if len(tail) > 0 else var

        if position_value is not None:
            return var * position_value, cvar * position_value
        return var, cvar

    def compute_parametric(
        self,
        confidence: float = 0.99,
        position_value: float | None = None,
    ) -> tuple[float, float]:
        """
        Parametric (Gaussian) VaR + CVaR.

        Assumes returns are normally distributed.
        Faster but underestimates tail risk.

        Args:
            confidence: Confidence level (0.99 = 99% VaR)
            position_value: If provided, return $ amounts

        Returns:
            (var, cvar) as decimals or USD amounts
        """
        self._stats["computations"] += 1
        if len(self._returns) < 30:
            return 0.0, 0.0

        returns = np.array(list(self._returns))
        mean = float(np.mean(returns))
        std = float(np.std(returns, ddof=1))

        if std == 0:
            return 0.0, 0.0

        # Z-score for confidence level (e.g., 99% → z = -2.326)
        from scipy.stats import norm  # type: ignore

        # Protect against edge cases in confidence level
        if confidence >= 1.0:
            log.warning(
                "invalid_confidence_level_for_var",
                confidence=confidence,
                note="Confidence level must be < 1.0, using 0.999 as fallback"
            )
            confidence = 0.999
        elif confidence <= 0.0:
            log.warning(
                "invalid_confidence_level_for_var",
                confidence=confidence,
                note="Confidence level must be > 0.0, using 0.99 as fallback"
            )
            confidence = 0.99

        try:
            z = norm.ppf(1 - confidence)
        except Exception as e:
            log.error(
                "var_calculation_error",
                confidence=confidence,
                error=str(e),
                note="Using conservative VaR estimate"
            )
            # Fallback to historical method or conservative estimate
            return self.compute_historical(confidence, position_value)

        var = mean + z * std

        # CVaR for normal distribution: mean - std * phi(z) / (1 - confidence)
        # where phi is the PDF
        denominator = (1 - confidence)
        if denominator <= 0:
            log.warning(
                "invalid_denominator_for_cvar",
                confidence=confidence,
                denominator=denominator,
                note="Using VaR as CVaR fallback"
            )
            cvar = var  # Fallback to VaR if denominator is invalid
        else:
            cvar = mean - std * norm.pdf(z) / denominator

        if position_value is not None:
            return var * position_value, cvar * position_value
        return float(var), float(cvar)

    def compute(
        self,
        confidence: float = 0.99,
        position_value: float | None = None,
        method: str = "historical",
    ) -> tuple[float, float]:
        """
        Compute VaR + CVaR using specified method.

        Args:
            confidence: 0.99 for 99% VaR
            position_value: If provided, return USD amounts
            method: 'historical' or 'parametric'

        Returns:
            (var, cvar) — negative values indicate losses
        """
        if method == "parametric":
            try:
                return self.compute_parametric(confidence, position_value)
            except ImportError:
                log.warning("scipy_not_available_using_historical")
                return self.compute_historical(confidence, position_value)
        return self.compute_historical(confidence, position_value)

    def get_stats(self) -> dict[str, int]:
        stats = self._stats.copy()
        stats["n_returns"] = len(self._returns)
        return stats
