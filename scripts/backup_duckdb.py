#!/usr/bin/env python3
"""Hermes DuckDB backup — consistent EXPORT DATABASE snapshot + rotation.

The durable store is a single file: data/hermes.duckdb. This script produces a
fully consistent snapshot via DuckDB's EXPORT DATABASE (all tables written to a
timestamped directory), then rotates old backups (keeps last N).

Restore:  duckdb restore.duckdb "IMPORT DATABASE '<backup_dir>'"

Usage:
    python scripts/backup_duckdb.py [--keep N] [--dest DIR]
Defaults: --keep 7, --dest data/backups
"""
from __future__ import annotations

import argparse
import datetime as _dt
import shutil
import sys
import os

import duckdb


def resolve_db_path() -> str:
    # Mirror hermes.web.status.get_duckdb_path without importing the whole app.
    env = os.environ.get("HERMES_DUCKDB_PATH")
    if env:
        return env
    # repo-relative default used by the stack
    cand = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "hermes.duckdb")
    return cand


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=7, help="backups to retain")
    ap.add_argument("--dest", default=None, help="backup root dir")
    ap.add_argument("--db", default=None, help="source duckdb path")
    args = ap.parse_args()

    db_path = args.db or resolve_db_path()
    if not os.path.exists(db_path):
        print(f"[backup] source not found: {db_path}", file=sys.stderr)
        return 2

    dest_root = args.dest or os.path.join(os.path.dirname(db_path), "backups")
    os.makedirs(dest_root, exist_ok=True)

    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    export_dir = os.path.join(dest_root, f"hermes.duckdb.{stamp}")
    if os.path.exists(export_dir):
        shutil.rmtree(export_dir)

    # Open read-only is fine for EXPORT; DuckDB serializes a consistent snapshot.
    con = duckdb.connect(db_path, read_only=True)
    try:
        con.execute(f"EXPORT DATABASE '{export_dir}' (FORMAT CSV, HEADER 1)")
        n_tables = len(con.execute("SHOW TABLES").fetchall())
    finally:
        con.close()

    size = sum(
        os.path.getsize(os.path.join(export_dir, f))
        for f in os.listdir(export_dir)
        if os.path.isfile(os.path.join(export_dir, f))
    )
    print(f"[backup] exported {n_tables} tables -> {export_dir} ({size//1024} KB)")

    # Rotate: keep newest --keep directories
    dirs = sorted(
        (d for d in os.listdir(dest_root) if d.startswith("hermes.duckdb.")),
        reverse=True,
    )
    for old in dirs[args.keep:]:
        old_path = os.path.join(dest_root, old)
        shutil.rmtree(old_path)
        print(f"[backup] rotated out: {old_path}")

    print(f"[backup] done. {min(len(dirs), args.keep)} backups retained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
