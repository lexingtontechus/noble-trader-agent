"""Bug report helper — capture redacted diagnostics and file a GitHub Issue.

Multi-tenant flow (per deployment design):
- A tenant's Hermes agent is self-hosted; proprietary code stays on tenant hardware.
- When a bug is hit, the agent captures structured, **redacted** diagnostics and opens
  a GitHub Issue against the maintainer's repo via the tenant's Git/pkg token
  (`secret:github.token`, env `GITHUB_TOKEN` — issued by the subscription process).
- Secrets are redacted with the existing `security_monitor._redact_sensitive_data`
  before anything leaves the tenant machine. The maintainer never sees raw creds.
- Tenants are *consumers of releases*, not forks: the Issue carries a regression-ready
  repro + version so the maintainer can fix + release a patch.
"""

from __future__ import annotations

import platform
import sys
import traceback
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from hermes import __version__
from hermes.core.config import load_config
from hermes.core.secrets import get_secret_or_none

log = structlog.get_logger(__name__)


def collect_diagnostics(include_log_tail: bool = True, extra: dict | None = None) -> dict[str, Any]:
    """Build a redacted diagnostics blob safe to send off-machine."""
    # Redaction helpers from the security monitor.
    from hermes.ops.security_monitor import get_security_monitor, _deep_copy_redact
    from hermes.core.secrets import get_secret_or_none

    sm = get_security_monitor()
    cfg = load_config()
    # Key-based (top-level secret-named keys) + recursive value-based redaction.
    redacted_cfg = _deep_copy_redact(sm._redact_sensitive_data(cfg.__dict__))

    git_token = get_secret_or_none("github.token", "")
    diag: dict[str, Any] = {
        "agent_version": __version__,
        "git_token_present": bool(git_token),
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "config": redacted_cfg,
    }
    if extra:
        diag["extra"] = _deep_copy_redact(extra)
    if include_log_tail:
        diag["recent_log"] = _tail_log()
    return diag


def _tail_log(path: str | None = None, lines: int = 200) -> str:
    """Return the tail of the main log file (best-effort, redacted)."""
    from hermes.ops.security_monitor import _deep_copy_redact

    import os

    log_path = path or os.environ.get("HERMES_LOG_PATH") or "./logs/hermes.log"
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            data = "".join(f.readlines()[-lines:])
        # redact inline secret-looking values
        return str(_deep_copy_redact({"log": data})["log"])
    except Exception as e:
        return f"(log unavailable: {e})"


def file_github_issue(
    repo: str,
    title: str,
    body: str,
    *,
    labels: list[str] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Open a GitHub Issue against `owner/repo` using the tenant's Git token.

    Returns the API response dict (includes `html_url`). Raises on non-2xx.
    """
    token = token or get_secret_or_none("github.token", "")
    if not token:
        raise RuntimeError(
            "No GitHub token configured (secret:github.token / env GITHUB_TOKEN). "
            "Add it via the wizard or `noble config` so bug reports can be filed."
        )
    if not repo or "/" not in repo:
        raise ValueError("repo must be 'owner/name'")

    payload: dict[str, Any] = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels

    url = f"https://api.github.com/repos/{repo}/issues"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"noble-trader-agent/{__version__}",
    }
    # Never log the token; httpx will not print it.
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, json=payload, headers=headers)
    if resp.status_code not in (200, 201):
        log.warning("github_issue_failed", status=resp.status_code, body=resp.text[:300])
        raise RuntimeError(f"GitHub issue create failed: {resp.status_code} {resp.text[:200]}")
    data = resp.json()
    log.info("github_issue_created", issue=data.get("number"), url=data.get("html_url"))
    return data


def build_issue_body(diag: dict[str, Any], description: str, traceback_text: str | None = None) -> str:
    """Render a Markdown issue body from diagnostics + user description."""
    import json

    body = [
        f"## Description",
        description.strip() or "(no description provided)",
        "",
        "## Environment",
        f"- agent_version: `{diag.get('agent_version')}`",
        f"- git_token_present: `{diag.get('git_token_present')}`",
        f"- python: `{diag.get('python')}`",
        f"- platform: `{diag.get('platform')}`",
        f"- captured_at: `{diag.get('captured_at')}`",
        "",
    ]
    if traceback_text:
        body += ["## Traceback", "```", traceback_text.strip(), "```", ""]
    body += [
        "## Redacted diagnostics",
        "```json",
        json.dumps(diag, indent=2, default=str)[:6000],
        "```",
    ]
    return "\n".join(body)
