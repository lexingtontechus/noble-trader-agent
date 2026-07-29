"""
Hot-reload support for the Hermes web dashboard.

This module provides template hot-reload capability for development.
It watches template files and clears Jinja2's template cache when changes
are detected, allowing live template updates without server restart.

Usage:
    import os
    from hermes.web.hot_reload import HotReload

    # Enable hot-reload (development only)
    if os.getenv("HERMES_HOT_RELOAD", "false").lower() == "true":
        HotReload.enable()
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Callable

import structlog

log = structlog.get_logger(__name__)

# Global state for hot-reload
_watcher: threading.Thread | None = None
_running = False
_template_dir: Path | None = None
_file_hashes: dict[str, str] = {}
_callbacks: list[Callable[[], None]] = []


class HotReload:
    """Template hot-reload manager for development."""

    @staticmethod
    def enable(
        template_dir: str | Path = "src/hermes/web/templates",
        poll_interval: float = 1.0,
        callback: Callable[[], None] | None = None,
    ) -> None:
        """Enable template hot-reload.

        Args:
            template_dir: Path to the templates directory
            poll_interval: How often to check for changes (seconds)
            callback: Optional callback to run on template change
        """
        global _watcher, _running, _template_dir, _callbacks

        if _running:
            log.debug("hot_reload_already_running")
            return

        _template_dir = Path(template_dir)
        if not _template_dir.exists():
            log.warning("hot_reload_template_dir_not_found", path=str(_template_dir))
            return

        _callbacks = [callback] if callback else []

        def watch_loop() -> None:
            """Background thread that watches for template changes."""
            global _running

            # Initial hash of all template files
            HotReload._scan_templates()

            log.info("hot_reload_started", template_dir=str(_template_dir))

            while _running:
                time.sleep(poll_interval)
                HotReload._check_for_changes()

        _running = True
        _watcher = threading.Thread(target=watch_loop, daemon=True)
        _watcher.start()

        log.info("hot_reload_enabled")

    @staticmethod
    def disable() -> None:
        """Disable template hot-reload."""
        global _watcher, _running

        _running = False
        if _watcher:
            _watcher.join(timeout=2.0)
            _watcher = None

        log.info("hot_reload_disabled")

    @staticmethod
    def _hash_file(path: Path) -> str:
        """Calculate MD5 hash of file contents."""
        try:
            content = path.read_bytes()
            return hashlib.md5(content).hexdigest()
        except Exception:
            return ""

    @staticmethod
    def _scan_templates() -> None:
        """Scan template directory and record initial hashes."""
        global _file_hashes

        if not _template_dir:
            return

        for pattern in ["**/*.html", "**/*.jinja2"]:
            for path in _template_dir.glob(pattern):
                _file_hashes[str(path)] = HotReload._hash_file(path)

    @staticmethod
    def _check_for_changes() -> None:
        """Check for template changes and trigger callbacks."""
        global _file_hashes

        if not _template_dir:
            return

        changed = False
        for pattern in ["**/*.html", "**/*.jinja2"]:
            for path in _template_dir.glob(pattern):
                path_str = str(path)
                current_hash = HotReload._hash_file(path)

                if path_str not in _file_hashes:
                    # New file
                    _file_hashes[path_str] = current_hash
                    changed = True
                    log.debug("hot_reload_new_file", path=path_str)
                elif _file_hashes[path_str] != current_hash:
                    # Modified file
                    _file_hashes[path_str] = current_hash
                    changed = True
                    log.debug("hot_reload_modified_file", path=path_str)

        if changed:
            log.info("hot_reload_templates_changed")
            # Clear Jinja2 template cache
            HotReload._clear_template_cache()
            # Run callbacks
            for callback in _callbacks:
                try:
                    callback()
                except Exception as e:
                    log.error("hot_reload_callback_error", error=str(e))

    @staticmethod
    def _clear_template_cache() -> None:
        """Clear Jinja2 template cache."""
        import importlib

        try:
            # Clear the cached template loader
            from fastapi.templating import Jinja2Templates

            # Force reload of Jinja2's internal cache
            if hasattr(Jinja2Templates, "_template_cache"):
                Jinja2Templates._template_cache.clear()

            # Also try to clear any module-level caches
            import sys

            for module_name in list(sys.modules.keys()):
                if "template" in module_name.lower():
                    try:
                        importlib.reload(sys.modules[module_name])
                    except Exception:
                        pass

        except Exception as e:
            log.debug("hot_reload_cache_clear_error", error=str(e))


def setup_hot_reload() -> None:
    """Setup hot-reload if enabled via environment variable."""
    if os.getenv("HERMES_HOT_RELOAD", "false").lower() == "true":
        HotReload.enable()


# Context manager for hot-reload
class HotReloadContext:
    """Context manager for hot-reload that auto-disables on exit."""

    def __init__(
        self,
        template_dir: str | Path = "src/hermes/web/templates",
        poll_interval: float = 1.0,
    ):
        self.template_dir = template_dir
        self.poll_interval = poll_interval

    def __enter__(self) -> "HotReloadContext":
        HotReload.enable(self.template_dir, self.poll_interval)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        HotReload.disable()