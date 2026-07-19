"""
Alerting system — Discord + Telegram notifications for critical events.

Sends alerts for:
- Circuit breaker trips
- Kill switch activation
- Dead man's switch activation
- Daily loss limit hit
- Large PnL swings
- Venue connectivity loss
- New hypothesis promoted to live

See roadmap §10 Phase 10.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

from hermes.core.config import HermesConfig
from hermes.core.secrets import get_secret_or_none

log = structlog.get_logger(__name__)


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


class Alert:
    """An alert to send via notification channels."""

    def __init__(
        self,
        title: str,
        message: str,
        severity: AlertSeverity = AlertSeverity.INFO,
        source: str = "hermes",
        data: dict | None = None,
    ) -> None:
        self.title = title
        self.message = message
        self.severity = severity
        self.source = source
        self.data = data or {}
        self.ts = datetime.now(timezone.utc)

    def to_discord(self) -> dict:
        """Format alert as Discord webhook payload."""
        colors = {
            AlertSeverity.INFO: 5814783,       # blue
            AlertSeverity.WARNING: 16776960,    # yellow
            AlertSeverity.CRITICAL: 15158332,   # red
            AlertSeverity.EMERGENCY: 15158332,  # red
        }

        fields = []
        for key, value in self.data.items():
            fields.append({"name": key, "value": str(value), "inline": True})

        return {
            "embeds": [{
                "title": f"[{self.severity.value.upper()}] {self.title}",
                "description": self.message,
                "color": colors.get(self.severity, 5814783),
                "timestamp": self.ts.isoformat(),
                "footer": {"text": f"Hermes · {self.source}"},
                "fields": fields[:25],  # Discord max 25 fields
            }]
        }

    def to_telegram(self) -> str:
        """Format alert as Telegram message text."""
        icons = {
            AlertSeverity.INFO: "ℹ️",
            AlertSeverity.WARNING: "⚠️",
            AlertSeverity.CRITICAL: "🚨",
            AlertSeverity.EMERGENCY: "🆘",
        }
        icon = icons.get(self.severity, "ℹ️")

        lines = [
            f"{icon} *{self.title}*",
            f"Severity: `{self.severity.value.upper()}`",
            f"Source: `{self.source}`",
            f"Time: `{self.ts.strftime('%Y-%m-%d %H:%M:%S UTC')}`",
            "",
            self.message,
        ]

        if self.data:
            lines.append("")
            for key, value in self.data.items():
                lines.append(f"  `{key}`: {value}")

        return "\n".join(lines)


class AlertManager:
    """
    Manages alert delivery to Discord and Telegram.

    Usage:
        manager = AlertManager(config)
        await manager.start()

        await manager.send_alert(Alert(
            title="Circuit Breaker Tripped",
            message="Portfolio DD exceeded 15%",
            severity=AlertSeverity.CRITICAL,
            data={"drawdown_pct": 16.5, "threshold": 15.0},
        ))
    """

    def __init__(self, config: HermesConfig) -> None:
        self._config = config

        notif_config = config.notifications if hasattr(config, "notifications") else {}
        discord_config = notif_config.get("discord", {}) if isinstance(notif_config, dict) else {}
        telegram_config = notif_config.get("telegram", {}) if isinstance(notif_config, dict) else {}

        self._discord_webhook = get_secret_or_none("discord.webhook_url", "")
        if self._discord_webhook and "<" in self._discord_webhook:
            self._discord_webhook = None

        self._telegram_token = get_secret_or_none("telegram.bot_token", "")
        if self._telegram_token and "<" in self._telegram_token:
            self._telegram_token = None

        # chat_id is required for Telegram delivery — the user pastes it into the
        # wizard alongside the bot token (same pattern as the Discord webhook URL).
        # It is stored as secret:telegram.chat_id and resolved here; it is NOT a
        # value the agent can discover on its own.
        self._telegram_chat_id = get_secret_or_none("telegram.chat_id", "")
        if self._telegram_chat_id and "<" in self._telegram_chat_id:
            self._telegram_chat_id = None

        self._discord_enabled = bool(self._discord_webhook)
        self._telegram_enabled = bool(self._telegram_token and self._telegram_chat_id)

        self._stats = {
            "alerts_sent": 0,
            "discord_sent": 0,
            "telegram_sent": 0,
            "errors": 0,
        }

    async def start(self) -> None:
        log.info(
            "alert_manager_started",
            discord_enabled=self._discord_enabled,
            telegram_enabled=self._telegram_enabled,
        )

    async def stop(self) -> None:
        log.info("alert_manager_stopped", stats=self._stats)

    async def send_alert(self, alert: Alert) -> None:
        """Send an alert to all configured channels."""
        self._stats["alerts_sent"] += 1

        log.info(
            "alert_sending",
            title=alert.title,
            severity=alert.severity.value,
            source=alert.source,
        )

        # Send to Discord
        if self._discord_enabled:
            try:
                await self._send_discord(alert)
                self._stats["discord_sent"] += 1
            except Exception as e:
                self._stats["errors"] += 1
                log.warning("discord_alert_failed", error=str(e))

        # Send to Telegram
        if self._telegram_enabled:
            try:
                await self._send_telegram(alert)
                self._stats["telegram_sent"] += 1
            except Exception as e:
                self._stats["errors"] += 1
                log.warning("telegram_alert_failed", error=str(e))

    async def _send_discord(self, alert: Alert) -> None:
        """Send alert to Discord via webhook."""
        import httpx

        payload = alert.to_discord()
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(self._discord_webhook, json=payload)
            if response.status_code not in (200, 204):
                log.warning("discord_webhook_error", status=response.status_code, body=response.text[:200])

    async def _send_telegram(self, alert: Alert) -> None:
        """Send alert to Telegram via bot API."""
        import httpx

        text = alert.to_telegram()
        url = f"https://api.telegram.org/bot{self._telegram_token}/sendMessage"
        payload = {
            "chat_id": self._telegram_chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code != 200:
                log.warning("telegram_api_error", status=response.status_code, body=response.text[:200])

    def is_discord_enabled(self) -> bool:
        return self._discord_enabled

    def is_telegram_enabled(self) -> bool:
        return self._telegram_enabled

    def get_stats(self) -> dict[str, Any]:
        return self._stats.copy()
