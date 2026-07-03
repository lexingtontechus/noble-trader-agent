"""
Replay / Forensic Mode — replays any historical session through the full stack.

Given a time range, replays:
1. Noble Trader heartbeats (from signal_heartbeats table)
2. Market data ticks (from Parquet)
3. Through the full L4→L5→L3 pipeline

Used for:
- Debugging ("what happened at 3:42 PM yesterday?")
- Postmortem analysis
- Strategy validation
- Compliance audit

See roadmap §10 Phase 10.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

from hermes.core.config import HermesConfig
from hermes.db.migrate import get_duckdb_path

log = structlog.get_logger(__name__)


class ReplayResult(BaseModel):
    """Result of a replay session."""

    replay_id: str = Field(default_factory=lambda: str(uuid4()))
    ts_started: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ts_finished: datetime | None = None

    # Scope
    start_ts: datetime
    end_ts: datetime
    symbols: list[str]

    # What was replayed
    n_heartbeats: int = 0
    n_signals: int = 0
    n_orders: int = 0
    n_fills: int = 0
    n_events: int = 0

    # Timeline
    timeline: list[dict] = Field(default_factory=list)

    # Errors
    errors: list[str] = Field(default_factory=list)


class ReplayEngine:
    """
    Replays a historical session for forensic analysis.

    Loads events from DuckDB in chronological order and reconstructs
    the full timeline of what happened.

    Usage:
        engine = ReplayEngine(config)
        result = await engine.replay(
            start=datetime(2026, 7, 1, 14, 0, tzinfo=timezone.utc),
            end=datetime(2026, 7, 1, 15, 0, tzinfo=timezone.utc),
            symbols=["BTC-PERP"],
        )
    """

    def __init__(self, config: HermesConfig) -> None:
        self._config = config
        self._db_path = get_duckdb_path(config)

    async def replay(
        self,
        start: datetime,
        end: datetime,
        symbols: list[str] | None = None,
    ) -> ReplayResult:
        """
        Replay a historical session.

        Loads and merges events from multiple DuckDB tables in time order:
        - signal_heartbeats (NT signals received)
        - trade_signals_blended (L4 output)
        - risk_decisions (L5 output)
        - orders (L3 output)
        - fills (L3 fills)
        - price_monitor_events (L2.8 events)
        - circuit_breaker_events (risk events)
        - account_snapshots (portfolio state)

        Args:
            start: Start datetime
            end: End datetime
            symbols: Filter by symbols (None = all)

        Returns:
            ReplayResult with full timeline
        """
        result = ReplayResult(
            start_ts=start,
            end_ts=end,
            symbols=symbols or [],
        )

        log.info(
            "replay_starting",
            replay_id=result.replay_id,
            start=start.isoformat(),
            end=end.isoformat(),
            symbols=symbols,
        )

        try:
            import duckdb

            if not self._db_path.exists():
                result.errors.append("DuckDB file not found")
                result.ts_finished = datetime.now(timezone.utc)
                return result

            with duckdb.connect(str(self._db_path), read_only=True) as conn:
                timeline: list[dict] = []

                # 1. Heartbeats
                heartbeats = self._load_heartbeats(conn, start, end, symbols)
                for hb in heartbeats:
                    timeline.append({
                        "ts": str(hb.get("ts_received", "")),
                        "type": "heartbeat",
                        "symbol": hb.get("symbol", ""),
                        "signal": hb.get("signal", ""),
                        "regime": hb.get("regime", ""),
                        "detail": f"NT signal: {hb.get('signal', '?')} {hb.get('symbol', '?')} "
                                  f"regime={hb.get('regime', '?')} conf={hb.get('regime_conf', 0):.2f}",
                    })
                result.n_heartbeats = len(heartbeats)

                # 2. Blended signals
                signals = self._load_signals(conn, start, end, symbols)
                for sig in signals:
                    timeline.append({
                        "ts": str(sig.get("ts_emitted", "")),
                        "type": "signal",
                        "symbol": sig.get("symbol", ""),
                        "detail": f"L4: {sig.get('direction', '?')} "
                                  f"strategy={sig.get('entry_strategy', '?')} "
                                  f"regime={sig.get('meta_regime', '?')} "
                                  f"size=${sig.get('final_size_usd', 0):.0f}",
                    })
                result.n_signals = len(signals)

                # 3. Risk decisions
                decisions = self._load_decisions(conn, start, end)
                for dec in decisions:
                    timeline.append({
                        "ts": str(dec.get("ts", "")),
                        "type": "risk_decision",
                        "detail": f"L5: {'APPROVED' if dec.get('approved') else 'REJECTED'} "
                                  f"${dec.get('approved_size_usd', 0):.0f} "
                                  f"tier={dec.get('autonomy_tier', 0)} "
                                  f"[{dec.get('reason', '')[:40]}]",
                    })

                # 4. Orders
                orders = self._load_orders(conn, start, end, symbols)
                for order in orders:
                    timeline.append({
                        "ts": str(order.get("ts_created", "")),
                        "type": "order",
                        "symbol": order.get("symbol", ""),
                        "detail": f"Order: {order.get('side', '?')} "
                                  f"{order.get('qty_requested', 0):.6f} "
                                  f"{order.get('symbol', '')} "
                                  f"status={order.get('status', '?')}",
                    })
                result.n_orders = len(orders)

                # 5. Fills
                fills = self._load_fills(conn, start, end, symbols)
                for fill in fills:
                    timeline.append({
                        "ts": str(fill.get("ts", "")),
                        "type": "fill",
                        "symbol": fill.get("symbol", ""),
                        "detail": f"Fill: {fill.get('side', '?')} "
                                  f"{fill.get('qty', 0):.6f} @ {fill.get('price', 0):.2f} "
                                  f"slip={fill.get('slippage_bps', 0):.1f}bps",
                    })
                result.n_fills = len(fills)

                # 6. Monitor events
                events = self._load_monitor_events(conn, start, end, symbols)
                for event in events:
                    timeline.append({
                        "ts": str(event.get("ts", "")),
                        "type": "monitor_event",
                        "symbol": event.get("symbol", ""),
                        "detail": f"Monitor: {event.get('event_type', '?')} "
                                  f"severity={event.get('severity', '?')} "
                                  f"price={event.get('last_price', 0):.2f}",
                    })
                result.n_events = len(events)

                # 7. Circuit breaker events
                breakers = self._load_breaker_events(conn, start, end)
                for brk in breakers:
                    timeline.append({
                        "ts": str(brk.get("ts", "")),
                        "type": "circuit_breaker",
                        "detail": f"CB: {brk.get('breaker_type', '?')} "
                                  f"level={brk.get('level', 0)} "
                                  f"action={brk.get('action_taken', '?')}",
                    })

                # 8. Account snapshots (summarize)
                snapshots = self._load_snapshots(conn, start, end)
                for snap in snapshots:
                    timeline.append({
                        "ts": str(snap.get("ts", "")),
                        "type": "snapshot",
                        "detail": f"Equity: ${snap.get('equity_total', 0):,.2f} "
                                  f"DD: {snap.get('drawdown_pct', 0)*100:.2f}% "
                                  f"Positions: {snap.get('n_open_positions', 0)}",
                    })

            # Sort timeline by timestamp
            timeline.sort(key=lambda x: x["ts"])
            result.timeline = timeline

        except Exception as e:
            result.errors.append(str(e))
            log.error("replay_failed", error=str(e))

        result.ts_finished = datetime.now(timezone.utc)

        log.info(
            "replay_complete",
            replay_id=result.replay_id,
            n_events=len(result.timeline),
            n_heartbeats=result.n_heartbeats,
            n_signals=result.n_signals,
            n_orders=result.n_orders,
            n_fills=result.n_fills,
        )

        return result

    @staticmethod
    def _load_heartbeats(conn, start, end, symbols):
        try:
            query = "SELECT * FROM signal_heartbeats WHERE ts_received >= ? AND ts_received <= ?"
            params = [start, end]
            if symbols:
                placeholders = ",".join(["?" for _ in symbols])
                query += f" AND symbol IN ({placeholders})"
                params.extend(symbols)
            query += " ORDER BY ts_received ASC"
            result = conn.execute(query, params).fetchdf()
            return result.to_dict("records") if not result.empty else []
        except Exception:
            return []

    @staticmethod
    def _load_signals(conn, start, end, symbols):
        try:
            query = "SELECT * FROM trade_signals_blended WHERE ts_emitted >= ? AND ts_emitted <= ?"
            params = [start, end]
            if symbols:
                placeholders = ",".join(["?" for _ in symbols])
                query += f" AND symbol IN ({placeholders})"
                params.extend(symbols)
            query += " ORDER BY ts_emitted ASC"
            result = conn.execute(query, params).fetchdf()
            return result.to_dict("records") if not result.empty else []
        except Exception:
            return []

    @staticmethod
    def _load_decisions(conn, start, end):
        try:
            result = conn.execute(
                "SELECT * FROM risk_decisions WHERE ts >= ? AND ts <= ? ORDER BY ts ASC",
                [start, end],
            ).fetchdf()
            return result.to_dict("records") if not result.empty else []
        except Exception:
            return []

    @staticmethod
    def _load_orders(conn, start, end, symbols):
        try:
            query = "SELECT * FROM orders WHERE ts_created >= ? AND ts_created <= ?"
            params = [start, end]
            if symbols:
                placeholders = ",".join(["?" for _ in symbols])
                query += f" AND symbol IN ({placeholders})"
                params.extend(symbols)
            query += " ORDER BY ts_created ASC"
            result = conn.execute(query, params).fetchdf()
            return result.to_dict("records") if not result.empty else []
        except Exception:
            return []

    @staticmethod
    def _load_fills(conn, start, end, symbols):
        try:
            query = "SELECT * FROM fills WHERE ts >= ? AND ts <= ?"
            params = [start, end]
            if symbols:
                placeholders = ",".join(["?" for _ in symbols])
                query += f" AND symbol IN ({placeholders})"
                params.extend(symbols)
            query += " ORDER BY ts ASC"
            result = conn.execute(query, params).fetchdf()
            return result.to_dict("records") if not result.empty else []
        except Exception:
            return []

    @staticmethod
    def _load_monitor_events(conn, start, end, symbols):
        try:
            query = "SELECT * FROM price_monitor_events WHERE ts >= ? AND ts <= ?"
            params = [start, end]
            if symbols:
                placeholders = ",".join(["?" for _ in symbols])
                query += f" AND symbol IN ({placeholders})"
                params.extend(symbols)
            query += " ORDER BY ts ASC"
            result = conn.execute(query, params).fetchdf()
            return result.to_dict("records") if not result.empty else []
        except Exception:
            return []

    @staticmethod
    def _load_breaker_events(conn, start, end):
        try:
            result = conn.execute(
                "SELECT * FROM circuit_breaker_events WHERE ts >= ? AND ts <= ? ORDER BY ts ASC",
                [start, end],
            ).fetchdf()
            return result.to_dict("records") if not result.empty else []
        except Exception:
            return []

    @staticmethod
    def _load_snapshots(conn, start, end):
        try:
            result = conn.execute(
                "SELECT * FROM account_snapshots WHERE ts >= ? AND ts <= ? ORDER BY ts ASC",
                [start, end],
            ).fetchdf()
            return result.to_dict("records") if not result.empty else []
        except Exception:
            return []
