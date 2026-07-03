"""
Standalone script to initialize DuckDB without going through the full CLI.

Useful for first-time setup or CI:
    python scripts/init_duckdb.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add src to path so we can import hermes without installing
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from hermes.core.config import load_config, get_config_hash  # noqa: E402
from hermes.core.logging import setup_logging  # noqa: E402
from hermes.db.migrate import apply_migrations, get_duckdb_path, get_table_info  # noqa: E402
import structlog  # noqa: E402

log = structlog.get_logger(__name__)


def main() -> int:
    print("Hermes DuckDB Initialization")
    print("=" * 50)

    # Load config
    try:
        config = load_config()
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        print("Make sure config/default.yaml exists.")
        return 1

    setup_logging(level=config.log_level, format="text", output="stdout")
    config_hash = get_config_hash(config)
    print(f"Config hash: {config_hash}")
    print(f"DuckDB path: {get_duckdb_path(config)}")
    print()

    # Apply migrations
    try:
        apply_migrations(config)
        print("Schema applied successfully.")
    except Exception as e:
        print(f"ERROR applying schema: {e}")
        return 1

    # Show table info
    print()
    print("Tables in DuckDB:")
    print("-" * 50)
    info = get_table_info(config)
    for table_name, row_count in sorted(info.items()):
        print(f"  {table_name:35} {row_count:>8} rows")
    print("-" * 50)
    print(f"Total: {len(info)} tables")
    print()
    print("Done. DuckDB is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
