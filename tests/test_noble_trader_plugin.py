"""Tests for the Noble Trader Hermes desktop plugin."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).parent.parent / ".hermes" / "plugins" / "noble-trader"
DASHBOARD_DIR = PLUGIN_DIR / "dashboard"
PLUGIN_JS = PLUGIN_DIR / "plugin.js"
DESKTOP_JS = PLUGIN_DIR / "desktop" / "plugin.js"


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------

class TestPluginManifest:
    """Validate dashboard/manifest.json structure."""

    def test_manifest_is_valid_json(self):
        manifest_path = DASHBOARD_DIR / "manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_manifest_required_fields(self):
        data = json.loads((DASHBOARD_DIR / "manifest.json").read_text())
        assert data["name"] == "noble-trader"
        assert "label" in data
        assert "description" in data
        assert data["entry"] == "../plugin.js"
        assert data["api"] == "plugin_api.py"

    def test_manifest_tab_config(self):
        data = json.loads((DASHBOARD_DIR / "manifest.json").read_text())
        assert isinstance(data["tab"], dict)
        assert data["tab"]["path"] == "/noble-trader"
        assert data["tab"]["position"] == "after:skills"


# ---------------------------------------------------------------------------
# Python plugin manifest
# ---------------------------------------------------------------------------

class TestPythonPluginManifest:
    """Validate plugin.yaml."""

    def test_plugin_yaml_valid(self):
        import yaml
        data = yaml.safe_load((PLUGIN_DIR / "plugin.yaml").read_text())
        assert data["name"] == "noble-trader-desktop"
        assert "1.0.0" in str(data["version"])

    def test_plugin_yaml_provides_tools(self):
        import yaml
        data = yaml.safe_load((PLUGIN_DIR / "plugin.yaml").read_text())
        tools = data.get("provides_tools", [])
        assert "noble_balance" in tools
        assert "noble_assets" in tools
        assert "noble_status" in tools


# ---------------------------------------------------------------------------
# plugin_api.py
# ---------------------------------------------------------------------------

class TestPluginApi:
    """Validate the dashboard plugin's backend API router."""

    @pytest.fixture(autouse=True)
    def _load_plugin_api(self):
        # Insert the dashboard dir into sys.path so plugin_api.py can be imported
        sys.path.insert(0, str(DASHBOARD_DIR))
        if "plugin_api" in sys.modules:
            del sys.modules["plugin_api"]
        import plugin_api
        self.plugin_api = plugin_api
        yield
        sys.path.pop(0)
        if "plugin_api" in sys.modules:
            del sys.modules["plugin_api"]

    def test_router_is_fastapi_router(self):
        from fastapi import APIRouter
        assert isinstance(self.plugin_api.router, APIRouter)

    def test_router_has_routes(self):
        routes = [r for r in self.plugin_api.router.routes if hasattr(r, "methods")]
        assert len(routes) >= 5, f"Expected at least 5 routes, got {len(routes)}"

    def test_route_functions_exist(self):
        assert callable(self.plugin_api.noble_plugin_health)
        assert callable(self.plugin_api.noble_config)
        assert callable(self.plugin_api.noble_setup_status)
        assert callable(self.plugin_api.noble_portfolio)
        assert callable(self.plugin_api.noble_status)


# ---------------------------------------------------------------------------
# __init__.py (Python plugin)
# ---------------------------------------------------------------------------

class TestPythonPluginInit:
    """Validate the Python plugin's __init__.py."""

    def test_has_register_function(self):
        sys.path.insert(0, str(PLUGIN_DIR))
        if "__init__" in sys.modules:
            # Use a unique name to avoid conflicts
            pass
        import importlib.util
        spec = importlib.util.spec_from_file_location("noble_trader_plugin_init", PLUGIN_DIR / "__init__.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert hasattr(mod, "register")
        assert hasattr(mod, "register_tools")
        sys.path.pop(0)

    def test_register_tools_registers_three_tools(self):
        """register_tools should register noble_balance, noble_assets, noble_status."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("noble_trader_plugin_test", PLUGIN_DIR / "__init__.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        registered = []

        class FakeCtx:
            def register_tool(self, **kwargs):
                registered.append(kwargs)
            def register(self, **kwargs):
                pass

        ctx = FakeCtx()
        mod.register_tools(ctx)
        tool_names = [r.get("name") for r in registered]
        assert "noble_balance" in tool_names
        assert "noble_assets" in tool_names
        assert "noble_status" in tool_names


class TestOnSessionStartHook:
    """Validate the on_session_start auto-start hook wiring (W1)."""

    def _load_mod(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("noble_trader_plugin_hook", PLUGIN_DIR / "__init__.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_resolve_watchdog_script_finds_deployed(self):
        import os
        mod = self._load_mod()
        path = mod._resolve_watchdog_script()
        assert os.path.exists(path), f"watchdog script not found at {path}"
        assert path.endswith("scripts/watchdog.sh")

    def test_register_registers_on_session_start_hook(self):
        mod = self._load_mod()
        called = []
        class FakeCtx:
            def register_tool(self, **k):
                pass
            def register_hook(self, name, cb):
                called.append((name, cb))
            def register(self, **k):
                pass
        mod.register(FakeCtx())
        assert ("on_session_start", mod._on_session_start) in called

    def test_on_session_start_hook_is_fire_and_forget(self):
        """The hook must return immediately and never raise."""
        mod = self._load_mod()
        mod._watchdog_launched = True  # simulate already-launched → no-op
        assert mod._on_session_start(session_id="test") is None


# ---------------------------------------------------------------------------
# Frontend (index.js)
# ---------------------------------------------------------------------------

class TestFrontend:
    """Validate the desktop-runtime plugin.js (F1 contract) exists and is valid."""

    def test_plugin_js_exists(self):
        assert PLUGIN_JS.exists(), "plugin.js (root) must exist"
        assert DESKTOP_JS.exists(), "desktop/plugin.js must exist"

    def test_plugin_js_uses_plugin_sdk(self):
        content = PLUGIN_JS.read_text(encoding="utf-8")
        assert "@hermes/plugin-sdk" in content, "Must import the Hermes plugin SDK"
        assert "register" in content, "Must register with the SDK"
        assert "export default plugin" in content, "Must default-export the plugin"

    def test_plugin_js_has_tabs(self):
        content = PLUGIN_JS.read_text(encoding="utf-8")
        # The plugin should render Portfolio, Setup, and Status tabs
        assert "Portfolio" in content or "portfolio" in content.lower()
        assert "Setup" in content or "setup" in content.lower()
        assert "Status" in content or "status" in content.lower()

    def test_plugin_js_is_valid_esm(self):
        import subprocess, tempfile, os

        # The Hermes loader parses plugin.js as an ES module; verify it parses.
        # Use a temp .mjs file + node --check (the stdin pipe method with
        # --input-type=module has false positives on Windows).
        content = PLUGIN_JS.read_text(encoding="utf-8")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mjs", delete=False, dir=str(PLUGIN_JS.parent)) as f:
            f.write(content)
            tmp_path = f.name
        try:
            proc = subprocess.run(
                ["node", "--check", tmp_path],
                capture_output=True, text=True, timeout=10,
            )
            assert proc.returncode == 0, f"plugin.js is not valid ESM: {proc.stderr[:300]}"
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Deployment script
# ---------------------------------------------------------------------------

class TestDeployScript:
    """Validate the deployment script."""

    def test_deploy_script_exists(self):
        path = Path(__file__).parent.parent / "scripts" / "deploy_desktop_plugin.py"
        assert path.exists(), "deploy_desktop_plugin.py must exist"

    def test_deploy_script_has_dry_run(self):
        path = Path(__file__).parent.parent / "scripts" / "deploy_desktop_plugin.py"
        content = path.read_text(encoding="utf-8")
        assert "--dry-run" in content
        assert "--all" in content

    def test_deploy_script_dry_run_works(self):
        import subprocess
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parent.parent / "scripts" / "deploy_desktop_plugin.py"),
             "--dry-run"],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8",  # Windows cp1252 can't encode Unicode output chars
        )
        assert result.returncode == 0
        assert "DRY RUN" in result.stdout or "dry run" in result.stdout.lower()


# ---------------------------------------------------------------------------
# pyproject.toml
# ---------------------------------------------------------------------------

class TestPyprojectToml:
    """Validate pyproject.toml includes the plugin in the wheel."""

    def test_force_include_present(self):
        import tomllib
        with open(Path(__file__).parent.parent / "pyproject.toml", "rb") as f:
            config = tomllib.load(f)
        wheel = config["tool"]["hatch"]["build"]["targets"]["wheel"]
        assert "force-include" in wheel
        assert any(".hermes/plugins" in src for src in wheel["force-include"])
