# License + Install Process — exact spec (built)

Simple, package-level, single-credential. No tiers, no separate HMAC license key.
The **Git/pkg token is the license**.

## 1. The single token

The subscription process issues ONE token: the **Git/pkg token** (a GitHub PAT or
package-registry token scoped to the `noble-trader-agent` package). It does three jobs:
- authenticates **package install / pull** from the private index,
- authenticates **`noble bug`** GitHub Issue filing,
- is the **entitlement proof** (the agent is entitled because it was installed with a
  valid token for the current published version).

Saved in the agent as `GITHUB_TOKEN` (secret resolver key `github.token`); the wizard
collects it. There is **no `LICENSE_KEY`** — that field was removed.

## 2. Install process (exact)

Post-subscription, the external site hands the tenant a one-line, token-scoped install:

```
pip install --index-url https://pip.nobetrader.com/simple/ \
    --token <GIT_TOKEN> noble-trader-agent==<VERSION>
```

(or a signed bootstrap `curl -sSL https://get.nobetrader.com | sh` that runs the same
command with the token). This replaces the old "repo + profile link" distribution.
The wheel is the distributed artifact; proprietary source is not handed to tenants as
a clone. Tenant config stays in `.env` / local DuckDB, never in the package.

## 3. Entitlement verification (built)

`src/hermes/core/entitlement.py` — two functions:

- `load_entitlement()` — **offline, startup-safe**. Token present? Logs
  `entitlement_ok` / `entitlement_missing`. **Never blocks** — a missing/invalid token
  warns, it does not brick the stack. This is what `web/app.py create_app` calls.
- `verify_entitlement()` — **live, on-demand** (run via `noble entitlement`). Proves the
  token authenticates to GitHub for our repo (`GET /user` + `GET /repos/{owner}/{repo}`).
  Fail-soft: network/offline → reports inconclusive, does not raise. Exits non-zero if
  the token is invalid/missing.

No tier logic, no version-gating beyond "token valid + repo reachable".

## 4. Bug flow (built, reuses the same token)

`noble bug --description "..." [--repo owner/name] [--dry-run]` collects **redacted**
diagnostics (config + env + log tail, via `security_monitor` redaction) and opens a
GitHub Issue with `GITHUB_TOKEN`. Tenants file Issues, not forks; the maintainer
reproduces from version + repro and ships a patch.

## 5. What is intentionally NOT built (per "keep it simple")

- No tiers / `is_entitled(min_tier)` gating.
- No separate HMAC license key (the Git token is the license).
- No private package registry stood up here (that is the external site's job); the
  agent only consumes the token. GitHub Packages is the natural index since the token
  is already a GitHub token.
- No auto-updater / plugin interface / Nuitka obfuscation — out of scope unless asked.

See `docs/roadmap.md` §15.8 for approval delivery, and CHANGELOG session 17.
