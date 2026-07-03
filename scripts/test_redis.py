"""
Standalone script to test Redis connectivity.

Tests both:
1. Hermes internal Redis (for pub/sub between layers)
2. Noble Trader upstream Redis (for heartbeat subscription)

Usage:
    python scripts/test_redis.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from hermes.core.config import load_config  # noqa: E402
from hermes.core.secrets import get_secret_or_none  # noqa: E402


def test_redis(url: str, label: str) -> bool:
    """Test a Redis connection with a ping + pub/sub round-trip."""
    try:
        import redis as redis_lib

        print(f"  Testing {label}: {url}")
        client = redis_lib.from_url(url, socket_connect_timeout=3)

        # Ping
        pong = client.ping()
        if not pong:
            print(f"    FAIL: did not respond PONG")
            return False
        print(f"    OK: ping → PONG")

        # Pub/sub round-trip
        channel = "hermes:test_channel"
        subscriber = client.pubsub()
        subscriber.subscribe(channel)

        # Small publish
        client.publish(channel, "hello from hermes")
        import time

        time.sleep(0.1)

        message = subscriber.get_message(timeout=1.0)
        if message and message.get("type") == "message":
            print(f"    OK: pub/sub round-trip → received '{message['data'].decode()}'")
        else:
            print(f"    WARN: pub/sub round-trip did not receive message (non-fatal)")

        subscriber.close()
        client.close()
        return True
    except ImportError:
        print(f"    SKIP: redis package not installed")
        return False
    except Exception as e:
        print(f"    FAIL: {e}")
        return False


def main() -> int:
    print("Hermes Redis Connectivity Test")
    print("=" * 50)

    try:
        config = load_config()
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        return 1

    # 1. Hermes internal Redis
    hermes_url = config.hermes_redis.get("url", "redis://localhost:6379/1")
    hermes_ok = test_redis(hermes_url, "Hermes internal Redis")

    print()

    # 2. Noble Trader upstream Redis
    nt_redis = config.upstream.get("noble_trader", {}).get("redis", {})
    nt_url = nt_redis.get("url", "")
    if nt_url.startswith("secret:"):
        nt_url = get_secret_or_none(nt_url[7:], "") or ""
    if "<" in nt_url:
        print(f"  SKIP Noble Trader Redis: still using placeholder URL")
        nt_ok = False
    else:
        nt_ok = test_redis(nt_url, "Noble Trader upstream Redis")

    print()
    print("=" * 50)
    print(f"Hermes Redis:  {'OK' if hermes_ok else 'FAIL'}")
    print(f"Noble Trader:  {'OK' if nt_ok else 'FAIL/SKIP'}")
    print()
    if not hermes_ok:
        print("Hermes Redis is required. To start a local Redis on Windows:")
        print("  Option A: Install Memurai (https://www.memurai.com/get-memurai)")
        print("  Option B: Use Docker: docker run -d -p 6379:6379 redis")
        print("  Option C: Use WSL2 + apt install redis")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
