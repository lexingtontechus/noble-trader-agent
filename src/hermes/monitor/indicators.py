"""
Real-Time Indicator Engine — computes technical indicators on rolling bar windows.

Indicators:
- ATR(14) — Average True Range
- EMA(20, 50, 200) — Exponential Moving Average
- RSI(14) — Relative Strength Index
- Realized Volatility (5m, 1h, 1d)
- VWAP deviation
- Rolling Hurst exponent (simplified R/S analysis)
- Z-score of last return vs 60d distribution
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from datetime import datetime

import numpy as np
import structlog

from hermes.schemas.market import Bar

log = structlog.get_logger(__name__)


class IndicatorEngine:
    """
    Computes technical indicators on rolling bar windows.

    Usage:
        engine = IndicatorEngine()
        engine.on_bar(bar)  # feed closed bars
        atr = engine.get_atr("BTC", "1m")  # ATR(14) on 1m bars
        ema = engine.get_ema("BTC", "1m", 20)
    """

    def __init__(self, max_bars_per_symbol: int = 500) -> None:
        self._max_bars = max_bars_per_symbol
        # (symbol, timeframe) -> deque[Bar]
        self._bars: dict[tuple[str, str], deque[Bar]] = defaultdict(
            lambda: deque(maxlen=max_bars_per_symbol)
        )

    def on_bar(self, bar: Bar) -> None:
        """Add a closed bar to the indicator cache."""
        if not bar.closed:
            return  # only process closed bars
        key = (bar.symbol, bar.timeframe)
        self._bars[key].append(bar)

    def get_bars(self, symbol: str, timeframe: str) -> list[Bar]:
        """Get cached bars for a symbol/timeframe."""
        return list(self._bars.get((symbol, timeframe), []))

    def get_atr(self, symbol: str, timeframe: str, period: int = 14) -> float | None:
        """
        Average True Range.

        ATR = SMA of True Range over `period` bars.
        True Range = max(high - low, |high - prev_close|, |low - prev_close|)
        """
        bars = self.get_bars(symbol, timeframe)
        if len(bars) < period + 1:
            return None

        trs = []
        for i in range(1, len(bars)):
            bar = bars[i]
            prev_close = bars[i - 1].close
            tr = max(
                bar.high - bar.low,
                abs(bar.high - prev_close),
                abs(bar.low - prev_close),
            )
            trs.append(tr)

        # Take last `period` TRs
        recent_trs = trs[-period:]
        return sum(recent_trs) / len(recent_trs)

    def get_ema(self, symbol: str, timeframe: str, period: int) -> float | None:
        """Exponential Moving Average."""
        bars = self.get_bars(symbol, timeframe)
        if len(bars) < period:
            return None

        closes = [b.close for b in bars[-period * 3:]]  # use 3x period for convergence
        multiplier = 2 / (period + 1)

        # Start with SMA of first `period` values
        ema = sum(closes[:period]) / period
        for close in closes[period:]:
            ema = (close - ema) * multiplier + ema

        return ema

    def get_rsi(self, symbol: str, timeframe: str, period: int = 14) -> float | None:
        """
        Relative Strength Index.

        RSI = 100 - (100 / (1 + RS))
        RS = avg_gain / avg_loss over `period` bars
        """
        bars = self.get_bars(symbol, timeframe)
        if len(bars) < period + 1:
            return None

        gains = []
        losses = []
        for i in range(1, len(bars)):
            change = bars[i].close - bars[i - 1].close
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))

        # Use SMA method (simple, not Wilder's smoothing)
        recent_gains = gains[-period:]
        recent_losses = losses[-period:]

        avg_gain = sum(recent_gains) / period
        avg_loss = sum(recent_losses) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def get_realized_vol(
        self, symbol: str, timeframe: str, window: int = 20
    ) -> float | None:
        """
        Realized volatility (annualized) from log returns.

        For 1m bars: annualized = vol * sqrt(60 * 24 * 365)
        For 1h bars: annualized = vol * sqrt(24 * 365)
        For 1d bars: annualized = vol * sqrt(365)
        """
        bars = self.get_bars(symbol, timeframe)
        if len(bars) < window + 1:
            return None

        closes = [b.close for b in bars[-(window + 1):]]
        log_returns = np.diff(np.log(closes))

        vol = np.std(log_returns, ddof=1)

        # Annualization factor based on timeframe
        from hermes.monitor.tick_aggregator import TIMEFRAME_SECONDS
        tf_seconds = TIMEFRAME_SECONDS.get(timeframe, 60)
        periods_per_year = (365 * 24 * 3600) / tf_seconds
        annualized_vol = vol * math.sqrt(periods_per_year)

        return float(annualized_vol)

    def get_vwap_deviation(self, symbol: str, timeframe: str, window: int = 20) -> float | None:
        """
        Deviation of current price from VWAP (in bps).

        Positive = price above VWAP (potentially overbought)
        Negative = price below VWAP (potentially oversold)
        """
        bars = self.get_bars(symbol, timeframe)
        if len(bars) < window:
            return None

        recent = bars[-window:]
        current_price = recent[-1].close

        # Volume-weighted average
        total_value = sum(b.close * b.volume for b in recent)
        total_volume = sum(b.volume for b in recent)

        if total_volume == 0:
            return None

        vwap = total_value / total_volume
        deviation_bps = ((current_price - vwap) / vwap) * 10000
        return deviation_bps

    def get_hurst_exponent(self, symbol: str, timeframe: str, max_lag: int = 20) -> float | None:
        """
        Simplified Hurst exponent via R/S analysis.

        H < 0.5: mean-reverting
        H = 0.5: random walk
        H > 0.5: trending

        Uses the simplified aggregated variance method.
        """
        bars = self.get_bars(symbol, timeframe)
        if len(bars) < max_lag * 2:
            return None

        closes = np.array([b.close for b in bars[-(max_lag * 4):]])
        log_returns = np.diff(np.log(closes))

        # R/S analysis for multiple lags
        lags = range(2, max_lag + 1)
        rs_values = []

        for lag in lags:
            # Split returns into chunks of size `lag`
            n_chunks = len(log_returns) // lag
            if n_chunks < 1:
                continue

            rs_list = []
            for i in range(n_chunks):
                chunk = log_returns[i * lag : (i + 1) * lag]
                mean = np.mean(chunk)
                deviations = np.cumsum(chunk - mean)
                r = np.max(deviations) - np.min(deviations)
                s = np.std(chunk, ddof=1)
                if s > 0:
                    rs_list.append(r / s)

            if rs_list:
                rs_values.append((np.log(lag), np.log(np.mean(rs_list))))

        if len(rs_values) < 3:
            return None

        # Linear regression: log(R/S) = H * log(n) + c
        x = np.array([v[0] for v in rs_values])
        y = np.array([v[1] for v in rs_values])
        hurst = np.polyfit(x, y, 1)[0]

        return float(hurst)

    def get_return_zscore(
        self, symbol: str, timeframe: str, window: int = 60
    ) -> float | None:
        """
        Z-score of the most recent bar's return vs the rolling distribution.

        |z| > 2 suggests an unusual move.
        """
        bars = self.get_bars(symbol, timeframe)
        if len(bars) < window + 1:
            return None

        closes = np.array([b.close for b in bars[-(window + 1):]])
        log_returns = np.diff(np.log(closes))

        latest_return = log_returns[-1]
        historical_returns = log_returns[:-1]

        mean = np.mean(historical_returns)
        std = np.std(historical_returns, ddof=1)

        if std == 0:
            return 0.0

        z = (latest_return - mean) / std
        return float(z)

    def get_all_indicators(self, symbol: str, timeframe: str) -> dict[str, float | None]:
        """Get all indicators for a symbol/timeframe in one call."""
        return {
            "atr_14": self.get_atr(symbol, timeframe, 14),
            "ema_20": self.get_ema(symbol, timeframe, 20),
            "ema_50": self.get_ema(symbol, timeframe, 50),
            "ema_200": self.get_ema(symbol, timeframe, 200),
            "rsi_14": self.get_rsi(symbol, timeframe, 14),
            "realized_vol": self.get_realized_vol(symbol, timeframe, 20),
            "vwap_deviation_bps": self.get_vwap_deviation(symbol, timeframe, 20),
            "hurst_exponent": self.get_hurst_exponent(symbol, timeframe, 20),
            "return_zscore": self.get_return_zscore(symbol, timeframe, 60),
        }
