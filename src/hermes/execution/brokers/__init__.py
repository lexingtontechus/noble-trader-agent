"""Execution broker adapters.

An `ExecutionBroker` is the live-order-execution abstraction (NOT the
market-data `VenueAdapter` in `hermes/transport/adapters`, which only streams
ticks/bars). The agent executes trades through one broker:

- `PaperTradingEngine`  — simulated fills (paper mode, default)
- `MetaApiBroker`       — live MT4/MT5 execution via MetaApi (live mode)

Both expose the same interface consumed by `ExecutionEngine`:
    connect() / disconnect()
    submit_order(order, current_price, annualized_vol=0.6)
    cancel_order(order_id) -> bool
    close_position(position_id, reason="")
"""

from hermes.execution.brokers.base import ExecutionBroker

__all__ = ["ExecutionBroker"]
