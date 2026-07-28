"""Tests for the two-pipeline liveness watchdogs + redis_subscriber no-fallback.

These verify the REAL watchdog state machines (alert on transition, no spam,
restore on recovery) without needing the full agent env: hermes.core.config
and hermes.ops.alerting are replaced by lightweight fakes via conftest-style
monkeypatching in this module.
"""
import asyncio
import types
import sys

import pytest


# ── Fake the heavy hermes.* deps the watchdogs import ───────────────────────
class _FakeHermesConfig:
    def __init__(self, upstream=None):
        self.upstream = upstream or {}


@pytest.fixture(autouse=True)
def _stub_hermes(monkeypatch):
    # Inject ONLY the leaf modules the watchdogs import. Do NOT replace the
    # real `hermes` package (it is installed editable and must stay importable
    # so `hermes.transport.*` resolves). We override hermes.core.config,
    # hermes.core.secrets, hermes.ops.alerting with lightweight fakes.
    fake_core_config = types.ModuleType("hermes.core.config")
    fake_core_config.HermesConfig = _FakeHermesConfig
    fake_core_secrets = types.ModuleType("hermes.core.secrets")
    fake_core_secrets.get_secret_or_none = lambda name, default="": default

    class AlertSeverity:
        INFO = "info"
        WARNING = "warning"
        CRITICAL = "critical"
        EMERGENCY = "emergency"

    class Alert:
        def __init__(self, title, message, severity=AlertSeverity.INFO, source="x", data=None):
            self.title = title
            self.message = message
            self.severity = severity
            self.source = source
            self.data = data or {}

    class AlertManager:
        def __init__(self, config=None):
            self.sent = []

        async def send_alert(self, alert):
            self.sent.append(alert)

    fake_ops_alerting = types.ModuleType("hermes.ops.alerting")
    fake_ops_alerting.AlertSeverity = AlertSeverity
    fake_ops_alerting.Alert = Alert
    fake_ops_alerting.AlertManager = AlertManager

    monkeypatch.setitem(sys.modules, "hermes.core.config", fake_core_config)
    monkeypatch.setitem(sys.modules, "hermes.core.secrets", fake_core_secrets)
    monkeypatch.setitem(sys.modules, "hermes.ops.alerting", fake_ops_alerting)

    return AlertSeverity, Alert, AlertManager


def _cfg(**kw):
    return _FakeHermesConfig(upstream={"noble_trader": kw})


class _FakeConsumer:
    def __init__(self):
        self._stats = {"last_frame_at": 0.0}
        self._running = True

    def get_stats(self):
        return dict(self._stats)


@pytest.mark.asyncio
async def test_pricing_sse_critical_then_restore(_stub_hermes):
    _, _, AlertManager = _stub_hermes
    from hermes.transport.pricing_sse_watchdog import PricingSSEWatchdog

    am = AlertManager()
    cons = _FakeConsumer()
    w = PricingSSEWatchdog(
        _cfg(sse_liveness_timeout_sec=90), cons, am,
        poll_interval_sec=0.01, now=lambda: 1000.0,
    )
    # 100s gap -> dead
    cons._stats["last_frame_at"] = 900.0
    await w._check_once()
    assert am.sent[-1].severity == "critical"
    n_crit = sum(1 for a in am.sent if a.severity == "critical")
    # repeat dead -> no re-alert
    await w._check_once()
    assert sum(1 for a in am.sent if a.severity == "critical") == n_crit
    # recovery
    cons._stats["last_frame_at"] = 1000.0
    await w._check_once()
    assert am.sent[-1].severity == "info"
    # re-death -> new critical
    cons._stats["last_frame_at"] = 800.0
    await w._check_once()
    assert am.sent[-1].severity == "critical"


@pytest.mark.asyncio
async def test_signal_drought_warning_then_restore(_stub_hermes):
    _, _, AlertManager = _stub_hermes
    from hermes.transport.signal_drought_watchdog import SignalDroughtWatchdog

    am = AlertManager()
    dw = SignalDroughtWatchdog(
        _cfg(signal_drought_alert_sec=14400), am,
        poll_interval_sec=0.01, now=lambda: 100000.0,
    )
    dw._last_signal_at = 100000.0 - 5000  # < 4h -> ok
    await dw._check_once()
    assert not am.sent
    dw._last_signal_at = 100000.0 - 15000  # > 4h -> warning
    await dw._check_once()
    assert am.sent[-1].severity == "warning"
    n_warn = sum(1 for a in am.sent if a.severity == "warning")
    await dw._check_once()  # no spam
    assert sum(1 for a in am.sent if a.severity == "warning") == n_warn
    await dw.mark_signal(100000.0)  # restore
    assert am.sent[-1].severity == "info"


@pytest.mark.asyncio
async def test_proxy_liveness_critical_then_restore(_stub_hermes):
    _, _, AlertManager = _stub_hermes
    from hermes.transport.proxy_liveness import ProxyLiveness

    am = AlertManager()
    pw = ProxyLiveness(
        _cfg(
            proxy_heartbeat_timeout_sec=480,
            proxy_delivery_table="proxy_delivery_log",
            supabase={"url": "https://x.supabase.co", "anon_key": "k"},
        ),
        am, poll_interval_sec=0.01, now=lambda: 200000.0,
    )
    # stale heartbeat (gap 1000 > 480)
    async def stale():
        return 199000.0
    pw._latest_heartbeat_ts = stale
    await pw._check_once()
    assert am.sent[-1].severity == "critical"
    # fresh
    async def fresh():
        return 200000.0
    pw._latest_heartbeat_ts = fresh
    await pw._check_once()
    assert am.sent[-1].severity == "info"


def test_redis_subscriber_no_fallback_to_raw_stream():
    import os
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root (tests/..)
    src_path = os.path.join(base, "src", "hermes", "transport", "redis_subscriber.py")
    src = open(src_path, encoding="utf-8").read()
    # Docstring must no longer describe a fallback.
    doc = src.split('"""')[1]
    assert "Fallback:" not in doc
    # _fallback_channel must equal the proxy channel (no upstream bypass).
    assert "_fallback_channel = self._primary_channel" in src
    # _resolve_channel returns only the proxy channel.
    assert "return self._primary_channel" in src
    # No code-level (non-comment) use of signal.raw.noble_trader as an endpoint.
    code_refs = [
        ln for ln in src.splitlines()
        if "signal.raw.noble_trader" in ln and not ln.strip().startswith("#")
        and "transport, not" not in ln and "backend->proxy" not in ln
        and "as a Redis" not in ln and "directly" not in ln
    ]
    assert not code_refs, f"unexpected code reference: {code_refs}"
