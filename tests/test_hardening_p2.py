"""P2 hardening tests: GF (duplicate-decision idempotency at L3).

Run: env -u PYTHONPATH ./.venv/Scripts/python.exe -m pytest tests/test_hardening_p2.py -q
"""

import tempfile
from pathlib import Path

import pytest

from hermes.core.config import load_config
from hermes.execution.orchestrator import ExecutionEngine
from hermes.portfolio.state import PortfolioStateService


@pytest.fixture
def engine(tmp_path, monkeypatch):
    from hermes.db import migrate as _mig

    def patched(c):
        return tmp_path / "p2.duckdb"

    monkeypatch.setattr(_mig, "get_duckdb_path", patched)
    cfg = load_config()
    _mig.apply_migrations(cfg)
    state = PortfolioStateService(initial_equity=10000, config_hash="t")
    eng = ExecutionEngine(cfg, state, paper_mode=True)
    # Isolate the idempotency check to the temp DB (ExecutionEngine binds
    # get_duckdb_path at import time, so override the resolved path here).
    eng._db_path = tmp_path / "p2.duckdb"
    return cfg, eng


def test_gf_idempotency_detects_executed_decision(engine):
    cfg, eng = engine
    # No orders yet -> not executed
    assert eng._decision_already_executed("dec-1") is False

    # Insert an order row tied to dec-1 (mirrors what ExecutionWriter does)
    from hermes.db.migrate import safe_duckdb_connect
    with safe_duckdb_connect(str(eng._db_path)) as conn:
        conn.execute(
            """
            INSERT INTO orders (order_id, trade_id, risk_decision_id, symbol, venue,
                                side, order_type, time_in_force, qty_requested,
                                qty_filled, status, ts_created, config_hash)
            VALUES ('o1', 't1', 'dec-1', 'BTC/USD', 'hyperliquid', 'buy',
                    'limit', 'gtc', 0.001, 0.001, 'filled', now(), 't')
            """
        )
    # Now the same decision_id must be detected as already executed
    assert eng._decision_already_executed("dec-1") is True
    # A different decision_id is still free
    assert eng._decision_already_executed("dec-2") is False


def test_gf_idempotency_empty_decision_id_safe(engine):
    cfg, eng = engine
    # Empty/None decision_id must not raise and must not block
    assert eng._decision_already_executed("") is False


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
