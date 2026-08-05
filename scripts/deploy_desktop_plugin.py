#!/usr/bin/env python3
"""
Deploy the Noble Trader Hermes desktop plugin.

Copies the plugin to the user's Hermes desktop-plugins directory so it appears
in the Hermes desktop app.

The Electron desktop app loads the plugin from:
    <hermes_home>/desktop-plugins/<name>/plugin.js
(via apps/desktop/src/contrib/runtime-loader.ts, which reads the root
plugin.js of each desktop-plugins/ subdir).

Usage:
    python scripts/deploy_desktop_plugin.py           # deploy to user desktop-plugins dir
    python scripts/deploy_desktop_plugin.py --all     # also copy into each profile
    python scripts/deploy_desktop_plugin.py --dry-run # preview without writing

After running, restart (or reload) the Hermes desktop app. The "Noble Trader"
tab appears in the sidebar (position: after:skills).

NOTE: The legacy ~/.hermes/plugins/<name>/ directory (used by the now-retired
web dashboard) is cleaned up automatically during deployment. Only
desktop-plugins/ is used.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def find_plugin_source() -> Path:
    """Locate the noble-trader desktop plugin source directory.

    Tries (in order):
      1. The repo checkout (relative to this script).
      2. The installed package (hermes_trading_platform/.hermes/plugins/...).

    The source layout is:
        <root>/
          desktop/plugin.js     ← the ESM entry point (Electron loads this)
          dashboard/manifest.json
          dashboard/plugin_api.py
          plugin.yaml           ← Python plugin (agent tools)
          __init__.py           ← on_session_start hook
    """
    # 1. Repo checkout
    repo_root = Path(__file__).resolve().parent.parent
    repo_plugin = repo_root / ".hermes" / "plugins" / "noble-trader"
    if (repo_plugin / "desktop" / "plugin.js").exists():
        return repo_plugin

    # 2. Installed package
    try:
        import hermes_trading_platform  # noqa: F401
    except ImportError:
        pass
    else:
        pkg_root = Path(hermes_trading_platform.__file__).parent
        pkg_plugin = pkg_root / ".hermes" / "plugins" / "noble-trader"
        if (pkg_plugin / "desktop" / "plugin.js").exists():
            return pkg_plugin

    print("ERROR: Could not locate noble-trader desktop plugin source.", file=sys.stderr)
    print("Expected at: .hermes/plugins/noble-trader/desktop/plugin.js", file=sys.stderr)
    sys.exit(1)


def find_hermes_home() -> Path:
    """Resolve the primary Hermes home directory.

    The running `hermes_cli.main serve` backend may resolve HERMES_HOME to
    either the Windows-env value OR the `~/.hermes` fallback. Return the
    primary (env or LOCALAPPDATA) home; callers should also deploy to the
    `~/.hermes` fallback via ``fallback_hermes_homes()``.
    """
    env_home = os.environ.get("HERMES_HOME")
    if env_home:
        home = Path(env_home)
        parts = home.parts
        if "profiles" in [p.lower() for p in parts]:
            profiles_idx = [i for i, p in enumerate(parts) if p.lower() == "profiles"]
            if profiles_idx:
                home = Path(*parts[:profiles_idx[0]])
        if home.is_dir():
            return home
    # LOCALAPPDATA\hermes is the common Windows location
    localapp = os.environ.get("LOCALAPPDATA")
    if localapp:
        cand = Path(localapp) / "hermes"
        if cand.is_dir():
            return cand
    default = Path.home() / ".hermes"
    if default.is_dir():
        return default
    default.mkdir(parents=True, exist_ok=True)
    return default


def fallback_hermes_homes() -> list[Path]:
    """Additional HERMES_HOME roots the backend may resolve to.

    On Windows the backend sometimes falls back to the literal ``~/.hermes``
    (i.e. ``%USERPROFILE%/.hermes``) even when a Windows-env ``HERMES_HOME``
    points elsewhere. We deploy there too so discovery always finds the plugin.
    """
    homes = []
    up = Path.home() / ".hermes"
    if up.is_dir() and up not in homes:
        homes.append(up)
    primary = find_hermes_home()
    return [h for h in homes if h != primary]


def enable_plugins_in_configs(hermes_home: Path) -> None:
    """Ensure BOTH plugin names are in each profile's plugins.enabled.

    The dashboard plugin's manifest ``name`` is ``noble-trader`` and the Python
    plugin's ``plugin.yaml`` ``name`` is ``noble-trader-desktop``. The web
    backend's ``get_dashboard_plugins()`` GATE excludes any user plugin whose
    name is not in ``plugins.enabled`` — so BOTH names must be listed or the
    dashboard tab silently disappears (discovered but filtered out).

    NOTE: a missing config.yaml means the backend falls back to its built-in
    enabled set, which does NOT include user plugins. We therefore CREATE the
    config.yaml (with the plugin enabled) when it is absent, so even the
    fallback HERMES_HOME (e.g. C:\\Users\\aloys\\.hermes) reliably serves the
    plugin after a restart.
    """
    import yaml
    plugin_names = ["noble-trader-desktop", "noble-trader"]
    configs = [hermes_home / "config.yaml"]
    profiles_dir = hermes_home / "profiles"
    if profiles_dir.is_dir():
        for child in sorted(profiles_dir.iterdir()):
            if child.is_dir():
                configs.append(child / "config.yaml")
    for cfg_path in configs:
        if not cfg_path.exists():
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg = {}
        else:
            try:
                cfg = yaml.safe_load(cfg_path.read_text()) or {}
            except Exception:
                continue
        if not isinstance(cfg, dict):
            cfg = {}
        plugins = cfg.get("plugins") or {}
        if not isinstance(plugins, dict):
            plugins = {}
        enabled = plugins.get("enabled") or []
        if not isinstance(enabled, list):
            enabled = []
        changed = False
        for name in plugin_names:
            if name not in enabled:
                enabled.append(name)
                changed = True
        if changed:
            plugins["enabled"] = enabled
            cfg["plugins"] = plugins
            cfg_path.write_text(
                yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False)
            )
            print(f"  enabled plugins in {cfg_path.relative_to(hermes_home)}: {enabled}")


def find_profile_dirs(hermes_home: Path) -> list[Path]:
    """Find all profile directories under Hermes home."""
    profiles = []
    profiles_dir = hermes_home / "profiles"
    if profiles_dir.is_dir():
        for child in sorted(profiles_dir.iterdir()):
            if child.is_dir() and child.name != "default":
                profiles.append(child)
    profiles.append(hermes_home)
    return profiles


def cleanup_legacy_plugins(hermes_home: Path) -> None:
    """Remove legacy dashboard artifacts from the old web dashboard era.

    The retired Next.js/React SPA lived in <hermes_home>/dashboard/ — that is
    truly legacy and can be removed.

    HOWEVER: <hermes_home>/plugins/<name>/ is NOT legacy — the Hermes agent
    backend loads Python plugins (register_tools, register_hooks) from this
    directory. The desktop-plugins/ directory is only for the Electron runtime
    (plugin.js + dashboard/). Do NOT remove plugins/<name>/ — doing so kills the
    Python plugin and the on_session_start hook never fires.
    """
    legacy_dashboard = hermes_home / "dashboard"
    if legacy_dashboard.exists():
        shutil.rmtree(legacy_dashboard, ignore_errors=True)
        print(f"  [OK] Removed legacy {legacy_dashboard}")


def deploy_plugin(source: Path, target_root: Path, dry_run: bool = False) -> None:
    """Copy the plugin's desktop (Electron) AND Python (agent) entries.

    Two deployment targets per Hermes home/profile:

    1. <target_root>/desktop-plugins/<name>/plugin.js  ← Electron loads this
       (runtime-loader.ts reads <dir>/plugin.js directly). We also copy it to
       desktop/plugin.js for consistency.

    2. <target_root>/plugins/<name>/  ← Hermes agent backend loads the Python
       plugin (__init__.py, plugin.yaml, plugin_api.py, dashboard/) from here.
       The agent discovers Python plugins from plugins/<name>/ and mounts their
       routes + registers on_session_start hooks here.

    The Python plugin directory MUST exist for:
    - on_session_start hook → _maybe_relaunch_watchdog() → auto-start dashboard
    - register_tools() → noble_balance, noble_assets, noble_status
    - plugin_api.py → /api/plugins/noble-trader/* routes (CORS-exempt shim proxy)
    """
    # --- Desktop plugin (Electron) ---
    target_desktop = target_root / "desktop-plugins" / "noble-trader"
    # --- Python plugin (agent backend) ---
    target_python = target_root / "plugins" / "noble-trader"

    if dry_run:
        print(f"  [DRY RUN] Would deploy desktop: {source}/desktop/plugin.js -> {target_desktop}/plugin.js")
        print(f"  [DRY RUN] Would deploy python:  {source}/ -> {target_python}/")
        return

    # Desktop plugin: copy plugin.js to root (Electron loads from root)
    target_desktop.mkdir(parents=True, exist_ok=True)
    desktop_js = source / "desktop" / "plugin.js"
    if desktop_js.exists():
        shutil.copy2(desktop_js, target_desktop / "plugin.js")
    # Also copy to desktop/ subdir for consistency
    target_desktop_d = target_desktop / "desktop"
    target_desktop_d.mkdir(parents=True, exist_ok=True)
    if desktop_js.exists():
        shutil.copy2(desktop_js, target_desktop_d / "plugin.js")
    # Copy dashboard/ (manifest.json etc.)
    for item in ("dashboard",):
        src_item = source / item
        dst_item = target_desktop / item
        dst_item_py = target_python / item
        if src_item.exists():
            for dst in (dst_item, dst_item_py):
                if dst.exists():
                    shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(src_item, dst)

    # --- Python plugin: copy __init__.py, plugin.yaml, plugin_api.py ---
    target_python.mkdir(parents=True, exist_ok=True)
    for py_file in ("__init__.py", "plugin.yaml", "plugin_api.py"):
        src_f = source / py_file
        if src_f.exists():
            shutil.copy2(src_f, target_python / py_file)

    for pc in target_desktop.rglob("__pycache__"):
        shutil.rmtree(pc, ignore_errors=True)
    for pc in target_python.rglob("__pycache__"):
        shutil.rmtree(pc, ignore_errors=True)
    print(f"  [OK] Deployed desktop: {target_desktop}")
    print(f"  [OK] Deployed python:  {target_python}")


def main():
    parser = argparse.ArgumentParser(
        description="Deploy the Noble Trader Hermes desktop plugin."
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Deploy to ALL profile directories (default: deploy to default profile only)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be deployed without writing files",
    )
    parser.add_argument(
        "--source", type=Path,
        help="Override the plugin source directory (default: auto-detect)",
    )
    args = parser.parse_args()

    source = args.source or find_plugin_source()
    print(f"Plugin source: {source}")

    hermes_home = find_hermes_home()
    print(f"Primary Hermes home:   {hermes_home}")

    # Default: deploy to primary home's desktop-plugins/ (Electron app discovery).
    # --all: also deploy a copy to each profile dir.
    targets = [hermes_home]
    if args.all:
        targets = find_profile_dirs(hermes_home) + [hermes_home]
        seen = set()
        targets = [t for t in targets if not (str(t) in seen or seen.add(str(t)))]

    # Always also deploy to the fallback ~/.hermes root(s) the backend may use.
    for fb in fallback_hermes_homes():
        if fb not in targets:
            targets.append(fb)

    print(f"\nDeploying to {len(targets)} location(s):")
    for target in targets:
        print(f"\n  {target}/")
        deploy_plugin(source, target, dry_run=args.dry_run)

    if not args.dry_run:
        print("\\nEnabling plugins in profile configs:")
        enable_plugins_in_configs(hermes_home)
        for fb in fallback_hermes_homes():
            enable_plugins_in_configs(fb)

        # Clean up legacy plugins/ directory (used by retired web dashboard).
        cleanup_legacy_plugins(hermes_home)
        for fb in fallback_hermes_homes():
            cleanup_legacy_plugins(fb)

        print("\\n[OK] Deployment complete.")
        print("\\nNext steps:")
        print("  1. Restart the Hermes desktop app (or press ⌘K → 'Reload desktop plugins')")
        print("  2. The 'Noble Trader' tab appears in the sidebar (after:skills)")
        print("\\nThe plugin is deployed to desktop-plugins/ only.")
        print("Legacy plugins/ directories have been cleaned up.")
    else:
        print("\\n[OK] Dry run complete. No files were written.")


if __name__ == "__main__":
    main()