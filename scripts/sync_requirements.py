"""
Sync requirements*.txt files from pyproject.toml.

This ensures pyproject.toml remains the single source of truth for dependencies.
Run after editing pyproject.toml:
    python scripts/sync_requirements.py

Writes:
    requirements.txt          (runtime deps only)
    requirements-dev.txt      (runtime + dev deps, includes -r requirements.txt)
    requirements-optional.txt (optional extras: supabase, venues)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"


def parse_pyproject() -> dict:
    with PYPROJECT.open("rb") as f:
        return tomllib.load(f)


def write_requirements(
    path: Path, deps: list[str], header_lines: list[str] | None = None
) -> None:
    """Write a requirements file with header + sorted deps."""
    lines = []
    if header_lines:
        lines.extend(header_lines)
        lines.append("")
    for dep in deps:
        lines.append(dep)
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Wrote {path.name} ({len(deps)} dependencies)")


def main() -> int:
    if not PYPROJECT.exists():
        print(f"ERROR: {PYPROJECT} not found", file=sys.stderr)
        return 1

    data = parse_pyproject()
    project = data.get("project", {})
    deps = project.get("dependencies", [])
    optional = project.get("optional-dependencies", {})

    runtime_header = [
        "# ============================================================",
        "# Hermes Trading Platform — Runtime Dependencies",
        "# ============================================================",
        "# This file is GENERATED from pyproject.toml. Do not edit directly.",
        "# Regenerate with: python scripts/sync_requirements.py",
        "# Source of truth: pyproject.toml [project.dependencies]",
        "# ============================================================",
    ]

    dev_header = [
        "# ============================================================",
        "# Hermes Trading Platform — Development Dependencies",
        "# ============================================================",
        "# This file is GENERATED from pyproject.toml. Do not edit directly.",
        "# Regenerate with: python scripts/sync_requirements.py",
        "# Includes runtime deps + dev/test/lint tools.",
        "# ============================================================",
    ]

    optional_header = [
        "# ============================================================",
        "# Hermes Trading Platform — Optional Dependencies",
        "# ============================================================",
        "# This file is GENERATED from pyproject.toml. Do not edit directly.",
        "# Regenerate with: python scripts/sync_requirements.py",
        "# Install only the extras you need.",
        "# ============================================================",
    ]

    print("Syncing requirements files from pyproject.toml...")

    # Runtime deps
    write_requirements(ROOT / "requirements.txt", deps, runtime_header)

    # Dev deps (include -r requirements.txt at top, then dev extras)
    dev_deps = ["-r requirements.txt", ""]
    dev_extras = []
    for extra_name in ("dev",):  # only the dev extra goes here
        dev_extras.extend(optional.get(extra_name, []))
    dev_deps.extend(dev_extras)
    write_requirements(ROOT / "requirements-dev.txt", dev_deps, dev_header)

    # Optional deps (all extras except dev)
    opt_deps = []
    for extra_name, extra_deps in optional.items():
        if extra_name == "dev":
            continue
        opt_deps.append(f"# --- {extra_name} ---")
        opt_deps.extend(extra_deps)
        opt_deps.append("")
    write_requirements(ROOT / "requirements-optional.txt", opt_deps, optional_header)

    print()
    print("Done. pyproject.toml is the source of truth; these files are generated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
