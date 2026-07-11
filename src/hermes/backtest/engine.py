"""
Event-Driven Backtest Engine — replays historical bars through the L4-L5 pipeline.

Supports two modes:
1. Bar replay — replays historical OHLCV bars from Parquet/DuckDB through
   the signal synthesizer + risk gate + paper execution engine.
2. Heartbeat replay — replays historical Noble Trader heartbeats from
   the signal_heartbeats table through the current Hermes stack.

See roadmap §2.5 + §10 Phase 7.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

from hermes.core.config import HermesConfig, get_config_hash
from hermes.db.migrate import get_duckdb_path
from hermes.execution.orchestrator import ExecutionEngine
from hermes.execution.orders import OrderStatus
from hermes.portfolio.orchestrator import PortfolioRiskEngine
from hermes.portfolio.state import PortfolioStateService
from hermes.schemas.heartbeat import NobleTraderHeartbeat
from hermes.schemas.market import Bar, Tick, Venue
from hermes.signals.synthesizer import BlendedSignal, SignalSynthesizer

log = structlog.get_logger(__name__)


class BacktestResult(BaseModel):
    """Result of a single backtest run."""

    run_id: str = Field(default_factory=lambda: str(uuid4()))
    ts_started: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ts_finished: datetime | None = None
    duration_sec: int = 0

    # Config
    mode: str  # bar_replay | heartbeat_replay
    start_ts: datetime
    end_ts: datetime
    symbols: list[str]
    initial_equity: float
    config_hash: str = ""

    # Performance
    n_heartbeats: int = 0
    n_signals_produced: int = 0
    n_signals_approved: int = 0
    n_signals_rejected: int = 0
    n_orders: int = 0
    n_fills: int = 0
    n_positions_closed: int = 0

    final_equity: float = 0
    total_return_pct: float = 0
    total_net_pnl: float = 0
    realized_pnl: float = 0
    unrealized_pnl: float = 0
    max_drawdown_pct: float = 0

    # Metrics (computed by tear sheet)
    tear_sheet: dict = Field(default_factory=dict)

    # Trades
    trades: list[dict] = Field(default_factory=list)

    # Error
    error: str | None = None


class BacktestEngine:
    """
    Event-driven backtest engine.

    Replays historical data through the full Hermes pipeline:
    L4 (synthesize) → L5 (risk) → L3 (execute paper) → PnL

    Usage:
        engine = BacktestEngine(config)
        result = await engine.run_heartbeat_replay(
            symbols=["BTC-PERP"],
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 2, 1, tzinfo=timezone.utc),
            initial_equity=100000,
        )
    """

    def __init__(self, config: HermesConfig) -> None:
        self._config = config
        self._db_path = get_duckdb_path(config)
        self._config_hash = get_config_hash(config)
        self._price_hours = 72  # lookback window for real price series

    async def run_heartbeat_replay(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        initial_equity: float = 100000,
        speed: float = 0.0,  # 0 = max speed, >0 = seconds between heartbeats
    ) -> BacktestResult:
        """
        Replay historical Noble Trader heartbeats through the current Hermes stack.

        Reads from signal_heartbeats table, reconstructs NobleTraderHeartbeat objects,
        feeds through SignalSynthesizer → PortfolioRiskEngine → ExecutionEngine.

        Args:
            symbols: Symbols to replay
            start: Start datetime
            end: End datetime
            initial_equity: Starting equity
            speed: Seconds to wait between heartbeats (0 = instant)

        Returns:
            BacktestResult with full metrics
        """
        result = BacktestResult(
            mode="heartbeat_replay",
            start_ts=start,
            end_ts=end,
            symbols=symbols,
            initial_equity=initial_equity,
            config_hash=self._config_hash,
        )

        log.info(
            "backtest_starting",
            run_id=result.run_id,
            mode="heartbeat_replay",
            symbols=symbols,
            start=start.isoformat(),
            end=end.isoformat(),
        )

        try:
            # Load heartbeats from DuckDB
            heartbeats = self._load_heartbeats(symbols, start, end)
            result.n_heartbeats = len(heartbeats)

            if not heartbeats:
                result.error = "no_heartbeats_found"
                result.ts_finished = datetime.now(timezone.utc)
                return result

            log.info("backtest_heartbeats_loaded", n=heartbeats.__len__())

            # Initialize pipeline components with temp DB
            import tempfile
            import shutil

            temp_dir = Path(tempfile.mkdtemp())
            temp_db = temp_dir / "backtest.duckdb"

            try:
                # Copy schema to temp DB
                import duckdb

                schema_file = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
                migrations_dir = Path(__file__).resolve().parent.parent / "db" / "migrations"

                with duckdb.connect(str(temp_db)) as conn:
                    conn.execute(schema_file.read_text(encoding="utf-8"))
                    for mig in sorted(migrations_dir.glob("*.sql")):
                        conn.execute(mig.read_text(encoding="utf-8"))

                # Initialize components
                portfolio_state = PortfolioStateService(
                    initial_equity=initial_equity,
                    config_hash=self._config_hash,
                )

                # Override db paths to use temp DB
                import hermes.db.migrate as migrate_mod
                original_get_path = migrate_mod.get_duckdb_path
                migrate_mod.get_duckdb_path = lambda c: temp_db

                synthesizer = SignalSynthesizer(self._config)
                synthesizer._db_path = temp_db

                risk_engine = PortfolioRiskEngine(self._config, initial_equity=initial_equity)
                # Backtest mode: auto-approve trade actions (bypass live autonomy gate)
                try:
                    risk_engine._autonomy_gate.set_paper_mode(True)
                except Exception:
                    pass
                risk_engine._db_path = temp_db
                risk_engine._snapshot_writer._db_path = temp_db
                risk_engine._state = portfolio_state  # share state

                exec_engine = ExecutionEngine(self._config, portfolio_state, paper_mode=True)
                exec_engine._db_path = temp_db
                exec_engine._writer._db_path = temp_db

                await synthesizer.start()
                await risk_engine.start()
                await exec_engine.start()

                # Fetch REAL price series per symbol (HL candleSnapshot / Alpaca bars)
                from hermes.marketdata.price_feed import fetch_price_series
                price_series = {}
                for _sym in sorted({hb_data.get("symbol") for hb_data in heartbeats}):
                    try:
                        ps = await fetch_price_series(_sym, hours=max(24, self._price_hours), config=self._config)
                        price_series[_sym] = ps
                        log.info("price_series_loaded", symbol=_sym, candles=len(ps.candles))
                    except Exception as e:
                        log.warning("price_series_failed", symbol=_sym, error=str(e)[:120])
                        price_series[_sym] = None

                # Process each heartbeat
                for hb_data in heartbeats:
                    try:
                        hb = NobleTraderHeartbeat(
                            type="heartbeat",
                            symbol=hb_data["symbol"],
                            ts=int(hb_data["ts_upstream"].timestamp() * 1000) if isinstance(hb_data.get("ts_upstream"), datetime) else hb_data.get("ts", 0),
                            regime=hb_data.get("regime", "unknown"),
                            regime_conf=hb_data.get("regime_conf", 0.5),
                            signal=hb_data.get("signal", "neutral"),
                            entry_price=hb_data.get("entry_price", 0),
                            stop_loss=hb_data.get("stop_loss", 0),
                            take_profit=hb_data.get("take_profit", 0),
                            aggression=hb_data.get("aggression", "mid"),
                            brick_size=hb_data.get("brick_size", 50),
                            sl_bricks=hb_data.get("sl_bricks", 3),
                            tp_bricks=hb_data.get("tp_bricks", 5),
                            kelly_f=hb_data.get("kelly_f", 0.1),
                            effective_kelly=hb_data.get("effective_kelly", 0.1),
                            ev=hb_data.get("ev", 0),
                            ev_per_dollar=hb_data.get("ev_per_dollar", 0),
                            p_win=hb_data.get("p_win", 0.5),
                            p_regime=hb_data.get("p_regime", 0.5),
                            p_imbalance=hb_data.get("p_imbalance", 0.5),
                            p_markov=hb_data.get("p_markov", 0.5),
                            ev_scale=hb_data.get("ev_scale", 1.0),
                            markov_current_state=hb_data.get("markov_current_state", "FLAT"),
                            regime_shift=(
                                "true" if hb_data.get("regime_shift", False) in (True, "true")
                                else "false"
                            ),
                            prev_regime=hb_data.get("prev_regime"),
                            shift_at=0,
                            shifts_24h=hb_data.get("shifts_24h", 0),
                        )

                        # L4: Synthesize
                        signal = await synthesizer.process_heartbeat(
                            hb, equity=portfolio_state._initial_equity
                        )
                        result.n_signals_produced += 1

                        # L5: Risk gate
                        decision = await risk_engine.evaluate_signal(signal)
                        if decision.approved:
                            result.n_signals_approved += 1
                        else:
                            result.n_signals_rejected += 1

                        # L3: Execute (paper)
                        if decision.approved:
                            orders = await exec_engine.execute_decision(
                                decision=decision,
                                signal=signal,
                                current_price=signal.nt_entry_price,
                            )
                            result.n_orders += len(orders)
                            result.n_fills += sum(
                                1 for o in orders if o.status == OrderStatus.FILLED
                            )

                        # Mark positions against the REAL price series for this symbol.
                        _ps = price_series.get(hb_data.get("symbol"))
                        if _ps and _ps.candles:
                            for _c in _ps.candles:
                                for _pos in list(portfolio_state.get_all_positions()):
                                    if _pos.symbol != hb_data.get("symbol"):
                                        continue
                                    portfolio_state.update_price(_pos.symbol, _c.close)
                                    _sl = getattr(_pos, "stop_loss", 0) or 0
                                    _tp = getattr(_pos, "take_profit", 0) or 0
                                    if _sl and _c.low <= _sl:
                                        portfolio_state.remove_position(_pos.position_id, _sl, "stop_loss_hit")
                                        break
                                    if _tp and _c.high >= _tp:
                                        portfolio_state.remove_position(_pos.position_id, _tp, "take_profit_hit")
                                        break
                                # Persist equity snapshot so the tear sheet has a real curve
                                _m = portfolio_state.get_metrics()
                                try:
                                    import duckdb as _duck
                                    with _duck.connect(str(temp_db)) as _c2:
                                        _c2.execute(
                                            "INSERT INTO account_snapshots "
                                            "(snapshot_id, ts, snapshot_type, equity_total, cash_usd, cash_usdc, "
                                            "margin_used, margin_available, leverage_gross, leverage_net, "
                                            "realized_pnl, unrealized_pnl, funding_pnl, fees_paid, "
                                            "gross_exposure_usd, net_exposure_usd, long_exposure_usd, short_exposure_usd, "
                                            "n_open_positions, n_venues, peak_equity, drawdown_pct, drawdown_usd, "
                                            "time_in_dd_sec, config_hash) "
                                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                            [
                                                f"bt_{_c.ts_ms}_{hb_data.get('symbol')}",
                                                __import__("datetime").datetime.fromtimestamp(_c.ts_ms / 1000, tz=__import__("datetime").timezone.utc),
                                                "on_event",
                                                float(_m.equity_total),
                                                float(_m.cash_usd),
                                                float(_m.cash_usdc),
                                                float(_m.margin_used),
                                                float(_m.margin_available),
                                                float(_m.leverage_gross),
                                                float(_m.leverage_net),
                                                float(_m.realized_pnl),
                                                float(_m.unrealized_pnl),
                                                float(_m.funding_pnl),
                                                float(_m.fees_paid),
                                                float(_m.gross_exposure_usd),
                                                float(_m.net_exposure_usd),
                                                float(_m.long_exposure_usd),
                                                float(_m.short_exposure_usd),
                                                int(_m.n_open_positions),
                                                int(_m.n_venues),
                                                float(_m.peak_equity),
                                                float(_m.drawdown_pct),
                                                float(_m.drawdown_usd),
                                                int(_m.time_in_dd_sec),
                                                self._config_hash or "backtest",
                                            ],
                                        )
                                except Exception as _se:
                                    log.debug("snapshot_write_failed", error=str(_se)[:80])
                        else:
                            for pos in portfolio_state.get_all_positions():
                                portfolio_state.update_price(pos.symbol, hb.entry_price)

                        if speed > 0:
                            await asyncio.sleep(speed)

                    except Exception as e:
                        log.warning("backtest_heartbeat_error", error=str(e), symbol=hb_data.get("symbol"))

                # Close remaining positions at last REAL price for that symbol
                for pos in list(portfolio_state.get_all_positions()):
                    _ps = price_series.get(pos.symbol)
                    exit_price = _ps.candles[-1].close if _ps and _ps.candles else pos.entry_price
                    portfolio_state.remove_position(pos.position_id, exit_price, "backtest_end")

                result.n_positions_closed = result.n_fills  # simplified

                # Compute final metrics
                metrics = portfolio_state.get_metrics()
                result.final_equity = metrics.equity_total
                result.total_return_pct = ((metrics.equity_total / initial_equity) - 1) * 100
                result.realized_pnl = metrics.realized_pnl
                result.unrealized_pnl = metrics.unrealized_pnl
                result.total_net_pnl = metrics.realized_pnl + metrics.unrealized_pnl
                result.max_drawdown_pct = metrics.drawdown_pct * 100

                # Generate tear sheet
                from hermes.analytics.pnl_service import PnLService
                from hermes.analytics.tear_sheet import TearSheet

                pnl_service = PnLService(self._config, portfolio_state)
                pnl_service._db_path = temp_db
                tear_sheet = TearSheet(pnl_service)

                # Load equity curve from temp DB
                with duckdb.connect(str(temp_db), read_only=True) as conn:
                    snap_result = conn.execute(
                        "SELECT ts, equity_total FROM account_snapshots ORDER BY ts ASC"
                    ).fetchall()
                    equity_curve = [(row[0], float(row[1])) for row in snap_result]

                    pnl_result = conn.execute(
                        "SELECT trade_id, symbol, regime_at_close, net_pnl, r_multiple, hold_duration_sec FROM pnl_realized"
                    ).fetchdf()

                trades = pnl_result.to_dict("records") if not pnl_result.empty else []
                result.trades = trades
                result.tear_sheet = tear_sheet.generate(
                    equity_curve=equity_curve,
                    realized_trades=trades,
                )

                await synthesizer.stop()
                await risk_engine.stop()
                await exec_engine.stop()

            finally:
                # Restore the global get_duckdb_path in FINALLY so it never leaks
                # to temp_db if an exception occurs mid-replay.
                migrate_mod.get_duckdb_path = original_get_path
                # Cleanup temp DB
                shutil.rmtree(temp_dir, ignore_errors=True)

        except Exception as e:
            result.error = str(e)
            log.error("backtest_failed", error=str(e), exc_info=True)

        result.ts_finished = datetime.now(timezone.utc)
        result.duration_sec = int((result.ts_finished - result.ts_started).total_seconds())

        # Write result to DuckDB
        self._write_backtest_result(result)

        log.info(
            "backtest_complete",
            run_id=result.run_id,
            n_heartbeats=result.n_heartbeats,
            n_signals=result.n_signals_produced,
            n_approved=result.n_signals_approved,
            n_orders=result.n_orders,
            final_equity=result.final_equity,
            total_return=result.total_return_pct,
            duration_sec=result.duration_sec,
        )

        return result

    def _load_heartbeats(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        """Load heartbeats from DuckDB signal_heartbeats table."""
        import duckdb

        if not self._db_path.exists():
            log.warning("db_not_found", path=str(self._db_path))
            return []

        try:
            with duckdb.connect(str(self._db_path), read_only=True) as conn:
                placeholders = ",".join(["?" for _ in symbols])
                result = conn.execute(
                    f"""
                    SELECT * FROM signal_heartbeats
                    WHERE symbol IN ({placeholders})
                      AND ts_received >= ?
                      AND ts_received <= ?
                      AND accepted = TRUE
                    ORDER BY ts_received ASC
                    """,
                    [*symbols, start, end],
                ).fetchdf()

                if result.empty:
                    return []
                return result.to_dict("records")
        except Exception as e:
            log.warning("load_heartbeats_failed", error=str(e))
            return []

    def _write_backtest_result(self, result: BacktestResult) -> None:
        """Write backtest result to DuckDB."""
        import duckdb

        try:
            with duckdb.connect(str(self._db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO backtest_runs (
                        run_id, ts_started, ts_finished, duration_sec,
                        mode, start_ts, end_ts, symbols, initial_equity,
                        n_heartbeats, n_signals_produced, n_signals_approved,
                        n_signals_rejected, n_orders, n_fills,
                        final_equity, total_return_pct, total_net_pnl,
                        max_drawdown_pct, tear_sheet, config_hash, error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        result.run_id,
                        result.ts_started,
                        result.ts_finished,
                        result.duration_sec,
                        result.mode,
                        result.start_ts,
                        result.end_ts,
                        result.symbols,
                        result.initial_equity,
                        result.n_heartbeats,
                        result.n_signals_produced,
                        result.n_signals_approved,
                        result.n_signals_rejected,
                        result.n_orders,
                        result.n_fills,
                        result.final_equity,
                        result.total_return_pct,
                        result.total_net_pnl,
                        result.max_drawdown_pct,
                        json.dumps(result.tear_sheet, default=str),
                        result.config_hash,
                        result.error,
                    ],
                )
        except Exception as e:
            log.warning("backtest_result_write_failed", error=str(e))
