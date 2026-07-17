"""
Symbol-key helpers — the exchange dimension.

TradingView (and most venues) qualify an instrument as ``EXCHANGE:SYMBOL``
(e.g. ``COINBASE:BTCUSD`` vs ``BINANCE:BTCUSD``). The SAME instrument
trades on multiple exchanges at *different* prices, and ``asset_class``
(crypto vs forex vs commodity) drives PnL math downstream. This module gives
the registry + adapters one place to:

  * split ``COINBASE:BTCUSD`` -> (exchange="COINBASE", bare="BTCUSD")
  * classify ``asset_class`` from the symbol (not "6-alpha == forex")
  * build a canonical ``symbol`` cell value (qualified when exchange known)
"""
from __future__ import annotations

from typing import NamedTuple

# Known crypto base assets (used to classify 6-char symbols correctly).
_CRYPTO_BASES = {
    "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "AVAX", "DOT",
    "MATIC", "LINK", "LTC", "BCH", "TRX", "UNI", "ATOM", "XLM",
    "NEAR", "APT", "ARB", "OP", "INJ", "SUI", "TIA", "SEI",
}
# Known commodity / metal bases.
_COMMODITY_BASES = {"XAU", "XAG", "XPT", "XPD", "WTI", "BRENT", "NATGAS", "COPPER"}


class SymbolKey(NamedTuple):
    exchange: str | None  # "COINBASE" | None
    bare: str             # "BTCUSD" | "EURUSD"
    qualified: str        # "COINBASE:BTCUSD" | "EURUSD"


def parse_symbol_key(raw: str) -> SymbolKey:
    """Split ``EXCHANGE:SYMBOL`` -> (exchange, bare, qualified).

    If no exchange qualifier is present, ``exchange`` is None and
    ``qualified == bare`` (legacy / non-exchange sources stay bare so the
    ``signal_heartbeats.symbol`` join key is unchanged).
    """
    raw = (raw or "").strip()
    if ":" in raw:
        # Collapse any double-qualification (BINANCE:BINANCE:BTCUSD ->
        # exchange=BINANCE, bare=BTCUSD) by splitting once and
        # re-parsing the bare part if it still contains ':'.
        exch, _, rest = raw.partition(":")
        if ":" in rest:
            _, _, rest = rest.partition(":")
        return SymbolKey(exchange=exch.upper() or None, bare=rest.upper(), qualified=raw.upper())
    up = raw.upper()
    return SymbolKey(exchange=None, bare=up, qualified=up)


def qualify_symbol(symbol: str, source_id: str | None = None,
                   exchange: str | None = None,
                   source_exchange: dict[str, str] | None = None,
                   default_exchange: str | None = None) -> str:
    """Qualify a bare symbol with its exchange (TradingView EXCHANGE:SYMBOL form).

    Precedence:
      1. explicit ``exchange`` arg (from EA payload / CLI),
      2. ``source_exchange`` map lookup by ``source_id`` (bridge / subscriber),
      3. ``default_exchange`` (env BRIDGE_DEFAULT_EXCHANGE).

    The exchange dimension is stored ONCE per (source_id -> exchange) mapping,
    NOT per symbol — so switching a feed's exchange is a one-line map change,
    not a per-symbol list to maintain. Returns the symbol unchanged if no
    exchange resolves or it is already qualified (idempotent).
    """
    sym = (symbol or "").strip()
    if not sym or ":" in sym:
        return sym.upper()
    resolved = exchange
    if not resolved and source_id and source_exchange:
        resolved = source_exchange.get(source_id)
    if not resolved:
        resolved = default_exchange
    if not resolved:
        return sym.upper()
    return f"{resolved.upper()}:{sym.upper()}"


def classify_asset_class(symbol: str) -> str:
    """Classify a (bare) symbol into an asset class.

    Rules (in order):
      * contains '/'  -> crypto pair if base in _CRYPTO_BASES else equity/other
      * bare base in _COMMODITY_BASES -> "commodities"
      * bare base in _CRYPTO_BASES    -> "crypto"
      * 6-char all-alpha (e.g. EURUSD) -> "forex"
      * 3-5 char all-alpha (e.g. AAPL)  -> "equities"
      * otherwise -> "crypto" (default for unknown tickers on crypto venues)
    """
    bare = parse_symbol_key(symbol).bare
    if "/" in bare:
        base = bare.split("/")[0]
        return "crypto" if base in _CRYPTO_BASES else "equities"
    # Strip a trailing quote ccy to find the base (BTCUSD -> BTC, EURUSD -> EUR)
    base = bare[:-3] if len(bare) == 6 and bare[-3:].isalpha() else bare
    if base in _COMMODITY_BASES:
        return "commodities"
    if base in _CRYPTO_BASES:
        return "crypto"
    if len(bare) == 6 and bare.isalpha():
        return "forex"
    if bare.isalpha() and 2 < len(bare) <= 5:
        return "equities"
    return "crypto"
