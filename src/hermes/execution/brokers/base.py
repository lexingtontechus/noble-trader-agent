"""ExecutionBroker — abstract interface for live order execution.

This is deliberately separate from `hermes/transport/adapters/base.py`
(`VenueAdapter`), which is a MARKET-DATA interface (stream ticks / bars /
order book). Trading (submit/cancel/close) is a different capability and has
its own lifecycle (broker connection, account sync, fill reconciliation).

`PaperTradingEngine` already conforms to this interface structurally; it is
used in paper mode. `MetaApiBroker` implements it for live MT4/MT5 execution.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ExecutionBroker(ABC):
    """Abstract base for trade-execution brokers."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish the broker/account connection (deploy + synchronize)."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Tear down the broker connection."""
        ...

    @abstractmethod
    async def submit_order(
        self,
        order: Any,  # hermes.execution.orders.Order
        current_price: float,
        annualized_vol: float = 0.60,
    ) -> None:
        """Submit an order for execution; mutate `order` status in place.

        On acceptance, set `order.venue_order_id` and transition the order
        state machine (e.g. SUBMITTED). For synchronously-fillable orders
        (market), the implementation may poll for the fill and transition to
        FILLED, emitting order events + fills via the registered callbacks.
        """
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True if cancelled."""
        ...

    @abstractmethod
    async def close_position(self, position_id: str, reason: str = "") -> None:
        """Close an open brokerage position by its broker position id."""
        ...

    # ── Optional helpers (default no-ops) ──────────────────────────────

    async def get_positions(self) -> list[dict]:
        """Return open brokerage positions (empty if unsupported)."""
        return []

    async def get_account_information(self) -> dict | None:
        """Return brokerage account info (None if unsupported)."""
        return None

    def get_order(self, order_id: str) -> Any | None:
        """Return a previously-submitted order by id (None if unknown)."""
        return None

    def get_stats(self) -> dict:
        """Return broker-specific stats (empty if none)."""
        return {}

    def set_callbacks(
        self,
        event_callback=None,
        fill_callback=None,
    ) -> None:
        """Register async callbacks: event_callback(order_id, event), fill_callback(fill)."""
        self._event_callback = event_callback
        self._fill_callback = fill_callback
