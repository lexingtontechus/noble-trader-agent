"""
DuckDB migration runner.

Applies base schema (schema.sql) + versioned migrations from migrations/ directory.
Tracks applied versions in schema_version table.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from hermes.core.config import HermesConfig
from hermes.core.secrets import get_secret_or_none

log = structlog.get_logger(__name__)

SCHEMA_FILE = Path(__file__).parent / "schema.sql"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def get_duckdb_path(config: HermesConfig) -> Path:
    """Get the DuckDB file path from config, resolving any secret: references."""
    raw_path = config.duckdb.get("path", "./data/hermes.duckdb")
    if raw_path.startswith("secret:"):
        raw_path = get_secret_or_none(raw_path[7:], "./data/hermes.duckdb")
    path = Path(raw_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def apply_migrations(config: HermesConfig) -> None:
    """
    Apply all pending migrations to the DuckDB database.

    1. Apply base schema.sql (creates tables if not exist, sets version=1)
    2. Apply any migrations from migrations/ directory with version > current
    """
    import duckdb

    db_path = get_duckdb_path(config)
    log.info("opening_duckdb", path=str(db_path))

    with duckdb.connect(str(db_path)) as conn:
        # Check current schema version
        try:
            result = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
            current_version = result[0] if result[0] is not None else 0
        except Exception:
            current_version = 0

        # 1. Apply base schema if not yet applied
        if current_version < 1:
            log.info("applying_base_schema", file=str(SCHEMA_FILE))
            schema_sql = SCHEMA_FILE.read_text(encoding="utf-8")
            conn.execute(schema_sql)
            log.info("base_schema_applied", version=1)
            current_version = 1

        # 2. Apply incremental migrations
        if MIGRATIONS_DIR.exists():
            migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
            for mig_file in migration_files:
                # Extract version from filename (e.g., "002_nt_supabase_mirrors.sql" → 2)
                try:
                    mig_version = int(mig_file.stem.split("_")[0])
                except ValueError:
                    log.warning("skipping_invalid_migration_filename", file=str(mig_file))
                    continue

                if mig_version <= current_version:
                    log.debug("migration_already_applied", version=mig_version)
                    continue

                log.info("applying_migration", version=mig_version, file=str(mig_file))
                migration_sql = mig_file.read_text(encoding="utf-8")
                conn.execute(migration_sql)
                log.info("migration_applied", version=mig_version)

        # Verify final version
        result = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        log.info("schema_version_now", version=result[0])


def get_table_info(config: HermesConfig) -> dict:
    """Return info about all tables in the DuckDB (for health check)."""
    import duckdb

    db_path = get_duckdb_path(config)
    with duckdb.connect(str(db_path), read_only=True) as conn:
        tables = conn.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main'
            ORDER BY table_name
            """
        ).fetchall()

        info = {}
        for (table_name,) in tables:
            count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            info[table_name] = count
        return info
