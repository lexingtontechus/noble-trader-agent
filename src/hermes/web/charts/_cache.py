"""
In-memory TTL cache for chart PNG bytes — no Redis dependency.

Each chart endpoint caches its PNG output for 60 seconds (default), keyed by
(chart_type, symbol, last_n). The cache is a plain dict guarded by the GIL
for reads; concurrent writes are safe (worst case: two threads render the
same PNG simultaneously, last writer wins).

Why not Redis? The dashboard is local-first (per DASHBOARD-UPGRADE-SCOPING.md
§3.3): the local hermes agent FastAPI process owns the chart cache. Adding
a Redis dependency just for chart caching would violate the local-first
principle and add a failure mode (Redis down → no charts).

Usage:
    from hermes.web.charts._cache import chart_cache

    png_bytes = chart_cache.get_or_render(
        key=("renko", "BTC-USD", 100),
        ttl_sec=60,
        render_fn=lambda: render_renko_png("BTC-USD", last_n=100),
    )
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

_lock = threading.Lock()
_store: dict[tuple, tuple[Any, float]] = {}
"""{(chart_type, symbol, *params): (png_bytes, expires_at)}"""


def get_or_render(key: tuple, ttl_sec: float, render_fn: Callable[[], bytes]) -> bytes:
    """Return cached bytes for `key` if fresh; else call render_fn(), cache, return.

    Thread-safe: the lock is held only for the dict lookup + insert, NOT for
    the render_fn() call itself. This means two concurrent cache-miss requests
    for the same key will both render (wasted work) but the cache will end up
    with the most-recent render's bytes — which is fine because the render is
    deterministic for a given key + data state.
    """
    now = time.time()
    with _lock:
        entry = _store.get(key)
        if entry is not None and now < entry[1]:
            return entry[0]  # cache hit

    # Cache miss — render outside the lock so concurrent requests for OTHER
    # keys aren't blocked by this render.
    value = render_fn()

    now = time.time()
    with _lock:
        _store[key] = (value, now + ttl_sec)
    return value


def invalidate(key: tuple | None = None) -> int:
    """Drop one key (or all entries if key=None). Returns number of entries dropped."""
    with _lock:
        if key is None:
            n = len(_store)
            _store.clear()
            return n
        if key in _store:
            del _store[key]
            return 1
        return 0


def stats() -> dict[str, Any]:
    """Return cache stats for debugging / monitoring."""
    now = time.time()
    with _lock:
        n_total = len(_store)
        n_fresh = sum(1 for _, exp in _store.values() if now < exp)
        n_stale = n_total - n_fresh
    return {"entries": n_total, "fresh": n_fresh, "stale": n_stale}


# Singleton-style module-level accessor (matches the scoping doc's API).
chart_cache = type("ChartCache", (), {
    "get_or_render": staticmethod(get_or_render),
    "invalidate":    staticmethod(invalidate),
    "stats":         staticmethod(stats),
})()
