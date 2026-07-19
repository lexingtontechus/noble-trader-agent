"""GE — pending human-approval queue.

When the autonomy gate classifies an action as tier-3 (requires_human_approval),
L5 used to return approved=False and L3 would silently drop it. Now the decision is
persisted here (DuckDB `pending_decisions`) and an alert is pushed to the configured
msg channel (Discord/Telegram). A human approves via `platform approve {id}` (CLI) or
a Discord reaction; the approved RiskDecision is re-published to `risk.decision.*` so
L3 executes it. Expired entries (past the decision deadline = autonomy.tier_3.
approval_decision_ttl_sec, default 300s / 5 min) are marked `expired` and can no longer
be approved.

The store is intentionally synchronous + DuckDB-backed (same connection helpers as the
rest of the stack) so both the L5 engine and the `approve` CLI command can use it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from hermes.db import migrate as _db_migrate
from hermes.db.migrate import safe_duckdb_connect

log = structlog.get_logger(__name__)


class PendingApprovals:
    """DuckDB-backed store for decisions awaiting human approval."""

    def __init__(self, config, approval_timeout_seconds: float = 300) -> None:
        self._config = config
        self._timeout_s = approval_timeout_seconds

    def _conn(self, read_only: bool = False):
        db_path = _db_migrate.get_duckdb_path(self._config)
        return safe_duckdb_connect(str(db_path), read_only=read_only)

    def store(
        self,
        decision: Any,
        symbol: str = "",
        venue: str = "",
        direction: str = "",
    ) -> None:
        """Persist a tier-3 decision as PENDING.

        expires_at = now + approval_timeout_seconds (default 300s / 5 min). The
        decision deadline is configurable via autonomy.tier_3.approval_decision_ttl_sec.
        A decision past its expires_at can no longer be approved (see approve()).
        """
        expires = datetime.now(timezone.utc) + timedelta(seconds=self._timeout_s)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO pending_decisions
                    (decision_id, signal_id, symbol, venue, direction,
                     requested_size_usd, approved_size_usd, autonomy_tier,
                     reason, payload, expires_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                [
                    decision.decision_id,
                    decision.signal_id,
                    symbol,
                    venue,
                    direction,
                    float(decision.requested_size_usd),
                    float(decision.approved_size_usd),
                    int(decision.autonomy_tier),
                    decision.reason,
                    json.dumps(decision.model_dump(mode="json")),
                    expires,
                ],
            )
        log.info(
            "pending_decision_stored",
            decision_id=decision.decision_id,
            symbol=symbol,
            expires_at=expires.isoformat(),
        )

    def get(self, decision_id: str) -> dict | None:
        with self._conn(read_only=True) as conn:
            row = conn.execute(
                "SELECT * FROM pending_decisions WHERE decision_id = ?", [decision_id]
            ).fetchone()
            if not row:
                return None
            cols = [d[0] for d in conn.description]
            return dict(zip(cols, row))

    def list_pending(self, include_expired: bool = False) -> list[dict]:
        # Self-sweep: mark any overdue decisions expired before listing, so the
        # UI/CLI never shows approvables that are past their decision deadline.
        self.expire_overdue()
        with self._conn(read_only=True) as conn:
            sql = "SELECT * FROM pending_decisions WHERE status = 'pending'"
            if not include_expired:
                sql += " AND expires_at > now()"
            sql += " ORDER BY created_at ASC"
            rows = conn.execute(sql).fetchall()
            cols = [d[0] for d in conn.description]
            return [dict(zip(cols, r)) for r in rows]

    def approve(self, decision_id: str) -> dict | None:
        """Mark a pending decision approved; return its payload for re-publish.

        Guards the decision deadline: if the decision is past expires_at (or already
        handled), it is NOT approved — expire_overdue() marks it first, then the SELECT
        excludes expired rows, so an expired decision returns None. A user therefore
        cannot approve a proposed trade after its TTL window.
        """
        # Mark any overdue decisions expired before attempting approval.
        self.expire_overdue()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT payload FROM pending_decisions "
                "WHERE decision_id = ? AND status = 'pending' AND expires_at > now()",
                [decision_id],
            ).fetchone()
            if not row:
                log.warning("pending_decision_approve_blocked", decision_id=decision_id,
                            reason="not pending or past decision deadline")
                return None
            payload = json.loads(row[0])
            payload["approved"] = True
            payload["status"] = "approved"
            payload["requires_human_approval"] = False
            payload["reason"] = "human_approved"
            conn.execute(
                "UPDATE pending_decisions SET status = 'approved' WHERE decision_id = ?",
                [decision_id],
            )
        log.info("pending_decision_approved", decision_id=decision_id)
        return payload

    def expire_overdue(self) -> None:
        """Mark pending decisions past their expiry as expired."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE pending_decisions SET status = 'expired' "
                "WHERE status = 'pending' AND expires_at <= now()"
            )
