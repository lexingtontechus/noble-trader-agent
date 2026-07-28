# Hermes Agent Skills — Manifest

**Status:** Phase 1A + 1B live — `trade_journal` + `signal-explainer` skills are wired; 9 skills are scoped contracts. Phase 1A cleanup pass (SQL three-valued-logic bug fix) + schema mismatch fix (migration 021) complete (2026-07-23).
**Services:** [`src/hermes/ops/trade_journal.py`](../src/hermes/ops/trade_journal.py) (`TradeJournal`) + [`src/hermes/ops/signal_explainer.py`](../src/hermes/ops/signal_explainer.py) (`SignalExplainer`)
**CLI:** [`src/hermes/commands/noble_cli.py`](../src/hermes/commands/noble_cli.py) — `noble journal generate` + `noble journal backfill` + `noble explanation generate` + `noble explanation backfill`
**Migrations:** [`019_llm_postmortem.sql`](../src/hermes/db/migrations/019_llm_postmortem.sql) + [`020_signal_explanations.sql`](../src/hermes/db/migrations/020_signal_explanations.sql) + [`021_pnl_realized_signal_id_exit_reason.sql`](../src/hermes/db/migrations/021_pnl_realized_signal_id_exit_reason.sql)

---

## What this directory is

Every skill in this directory is a self-contained instruction file
(`SKILL.md` + optional `references/*.md` + `examples/*.md`) that tells the
Hermes agent **what to do, what to learn, and what to generate** when
invoked. The Hermes agent reads a SKILL.md as operating instructions and
runs its own reasoning process against the input payload. There is no
separate Python "skill loader" class — the agent owns the execution
loop.

For `trade_journal` (Phase 1A), the flow is:

1. The agent's cron calls `noble journal generate --date YYYY-MM-DD` (or
   `noble journal backfill --start ... --end ... [--retry-failed]`).
2. The CLI dispatches to `TradeJournal.generate_postmortem_for_day()` /
   `TradeJournal.backfill()`.
3. `TradeJournal` SELECTs signals needing postmortems from
   `trade_signals_blended` JOIN `pnl_realized` LEFT JOIN
   `trade_postmortem`.
4. For each row, `TradeJournal` builds a payload and calls
   `skill_invoker(skill_md_path, payload)` — the `skill_invoker` is
   the agent's own inference router, injected via the `TradeJournal`
   constructor by the agent runtime.
5. The skill (this directory's `trade_journal/SKILL.md`) tells the
   agent how to read the payload and what JSON to return.
6. `TradeJournal` UPDATEs `trade_postmortem` with the result
   (`postmortem_llm`, `hypothesis`, `postmortem_status='generated'`,
   `postmortem_generated_at`, `prompt_tokens`, `completion_tokens`).
   On failure: `postmortem_status='llm_unavailable'` (retryable on the
   next `backfill --retry-failed` run).

**Skills are NOT prompt templates.** They are the only place the
program logic lives. Python code (`TradeJournal`, `SignalExplainer`,
the CLI) is just the plumbing — SELECT rows, build payload, call
invoker, INSERT/UPDATE row. The skill itself runs in the agent's
reasoning loop.

For `signal-explainer` (Phase 1B), the same flow runs against
`signal_heartbeats` instead of `trade_signals_blended`:

1. The agent's cron calls `noble explanation generate --date YYYY-MM-DD`
   (or `noble explanation backfill --start ... --end ... [--retry-failed]`).
2. The CLI dispatches to `SignalExplainer.generate_explanations_for_day()`
   / `SignalExplainer.backfill()`.
3. `SignalExplainer` SELECTs heartbeats needing explanations from
   `signal_heartbeats` LEFT JOIN `signal_explanations` LEFT JOIN
   LATERAL `meta_regime_history` (latest snapshot at or before
   `ts_received`).
4. For each row, `SignalExplainer` builds a payload and calls
   `skill_invoker(skill_md_path, payload)` — same constructor-injected
   seam as `TradeJournal`.
5. The skill (`signal-explainer/SKILL.md`) tells the agent how to
   read the payload and what JSON to return.
6. `SignalExplainer` INSERT/UPDATEs `signal_explanations` with the
   result (`rationale`, `explanation`, `source_breakdown`,
   `explanation_status='generated'`, `explanation_generated_at`,
   `prompt_tokens`, `completion_tokens`). On failure:
   `explanation_status='llm_unavailable'` (retryable on the next
   `backfill --retry-failed` run).

## Phasing

| Phase | Status   | Skills                                                |
|-------|----------|-------------------------------------------------------|
| 1A    | **Live** (cleanup pass + schema fix done 2026-07-23) | `trade_journal`                                       |
| 1B    | **Live** | `signal-explainer`                                    |
| 2     | Scoped   | `narrative-classifier`                                |
| 3     | Scoped   | `weight-optimizer`                                    |
| —     | Scoped   | `anomaly-explainer`, `risk-decision-explainer`, `breaker-narrator`, `tear-sheet-narrator`, `backtest-rigor-reviewer`, `eod-briefing`, `bug-report-summarizer` |

---

## Skill inventory (11 skills)

Each row maps a skill to its integration point in the agent codebase.
Skills marked **Live** are wired in today; skills marked **Scoped** have
a SKILL.md contract ready but no Python integration yet — they wait on
their phase to be promoted.

| # | Slug                       | Phase  | Status   | Trigger                                  | Caller (Python)                                                | Writes to (DuckDB)                                              | Reads from (DuckDB)                                          |
|---|----------------------------|--------|----------|------------------------------------------|----------------------------------------------------------------|-----------------------------------------------------------------|--------------------------------------------------------------|
| 1 | `trade_journal`            | 1A     | **Live** | Nightly cron + retry cron                | `ops/trade_journal.py::TradeJournal` via `noble journal` CLI   | `trade_postmortem` (1:1 with `trade_signals_blended`)           | `trade_signals_blended`, `pnl_realized`                      |
| 2 | `signal-explainer`         | 1B     | **Live** | Nightly cron + retry cron                | `ops/signal_explainer.py::SignalExplainer` via `noble explanation` CLI | `signal_explanations` (1:1 with `signal_heartbeats`)            | `signal_heartbeats`, `meta_regime_history`                   |
| 3 | `narrative-classifier`     | 2      | Scoped   | Continuous (with TTL cache)              | new `sources/narrative.py` (future)                            | new `narrative_signals` table                                   | external news / filings feed                                 |
| 4 | `weight-optimizer`         | 3      | Scoped   | Weekly cron                              | new `analysis/weight_optimizer.py` (future)                    | new `weight_proposals` table; writes GitHub PR                  | `pnl_realized`, `signal_heartbeats`                          |
| 5 | `anomaly-explainer`        | —      | Scoped   | On-anomaly                               | `monitor/anomaly_detector.py::AnomalyDetector.on_event` (future) | new `anomaly_explanations` table                                | `monitor_events`, `account_snapshots`                        |
| 6 | `risk-decision-explainer`  | —      | Scoped   | On risk-decision                         | `portfolio/risk_gate.py::RiskGate.evaluate` (future)           | `risk_decisions.reason_llm` + `risk_decisions.rationale_llm`    | `risk_decisions`, `account_snapshots`, `circuit_breaker_events` |
| 7 | `breaker-narrator`         | —      | Scoped   | On breaker trigger                       | `portfolio/circuit_breakers.py::CircuitBreaker.trigger` (future) | `circuit_breaker_events.payload->narrative_llm`                 | `circuit_breaker_events`, `account_snapshots`                |
| 8 | `tear-sheet-narrator`      | —      | Scoped   | On tear-sheet generation                 | `analytics/tear_sheet.py::TearSheet.generate` (future)         | new `tear_sheet_narratives` table                               | `pnl_realized`, `account_snapshots`, `signal_heartbeats`     |
| 9 | `backtest-rigor-reviewer`  | —      | Scoped   | On backtest completion                   | `backtest/statistics.py::RigorChecker.run` (future)            | new `backtest_reviews` table                                    | `backtest_runs`, `backtest_statistics`                       |
| 10| `eod-briefing`             | —      | Scoped   | EOD cron (after `trade_journal` finishes) | new `ops/eod_briefing.py` (future)                             | new `eod_briefings` table                                       | `trade_postmortem`, `account_snapshots`, `risk_decisions`    |
| 11| `bug-report-summarizer`    | —      | Scoped   | On `noble bug` invocation                | `ops/bug_report.py::BugReport.generate` (future)               | new `bug_report_summaries` table                                | `audit_log`, `circuit_breaker_events`, `risk_decisions`, recent logs |

## How to add a new skill

1. `mkdir skills/<slug>/`
2. Copy `trade_journal/SKILL.md` as a template (it is the canonical
   format). Edit:
   - YAML frontmatter (name, slug, version, description)
   - When / Architecture / Scope / Workflow / Output Schema sections
3. Add `references/*.md` for any reference material the agent should
   consult (schemas, formulas, decomposition models, lifecycle
   diagrams).
4. Add `examples/*.md` with 2-3 worked input → output examples.
5. Add a row to the inventory table above with the Python caller +
   DuckDB write/read targets.
6. Wire the Python caller — instantiate a service class that accepts
   a `skill_invoker` callable (constructor-injected by the agent
   runtime) and follows the same SELECT → build payload → call
   invoker → UPDATE pattern as `TradeJournal`.
7. Add a CLI subcommand under `noble` (or the appropriate group) that
   dispatches to the new service.

## Skill format contract

Every `SKILL.md` MUST have:

- **YAML frontmatter** with: `name`, `slug`, `version`, `description`.
- **`## When to Use`** — what triggers the skill (cron, event,
  operator command).
- **`## Architecture`** — what tables it reads, what it writes, what
  caller invokes it.
- **`## Scope`** — two lists: "This skill ONLY" / "This skill NEVER".
  The NEVER list MUST include "Makes any external API call other than
  the agent's own inference router".
- **`## Workflow`** — pseudocode for the caller, showing the payload
  schema + the DuckDB write site. References the `skill_invoker`
  callable, not a Python class.
- **`## Output Schema`** — the JSON shape the agent must produce.
- **`## References`** — pointer to `references/*.md`.
- **`## Examples`** — pointer to `examples/*.md`.

## Cross-references

- Services:
  - [`src/hermes/ops/trade_journal.py`](../src/hermes/ops/trade_journal.py) — `TradeJournal` class (Phase 1A)
  - [`src/hermes/ops/signal_explainer.py`](../src/hermes/ops/signal_explainer.py) — `SignalExplainer` class (Phase 1B)
- CLI: [`src/hermes/commands/noble_cli.py`](../src/hermes/commands/noble_cli.py) — `noble journal generate` + `noble journal backfill` + `noble explanation generate` + `noble explanation backfill`
- Migration 019: [`src/hermes/db/migrations/019_llm_postmortem.sql`](../src/hermes/db/migrations/019_llm_postmortem.sql) — creates `trade_postmortem` table, drops `hermes_hypotheses`, drops v3-era LLM columns from `trade_journal`
- Migration 020: [`src/hermes/db/migrations/020_signal_explanations.sql`](../src/hermes/db/migrations/020_signal_explanations.sql) — creates `signal_explanations` table (FK → `signal_heartbeats.heartbeat_id`)
- Migration 021: [`src/hermes/db/migrations/021_pnl_realized_signal_id_exit_reason.sql`](../src/hermes/db/migrations/021_pnl_realized_signal_id_exit_reason.sql) — adds `signal_id` + `exit_reason` columns to `pnl_realized` so `TradeJournal._select_pending()` can JOIN on `signal_id` and SELECT `exit_reason` (Phase 1A cleanup follow-up — resolves the pre-existing schema mismatch)
- Smoke tests: [`scripts/smoke_test_phase1a_trade_journal.py`](../../scripts/smoke_test_phase1a_trade_journal.py) (8 tests) + [`scripts/smoke_test_phase1b_signal_explainer.py`](../../scripts/smoke_test_phase1b_signal_explainer.py) (7 tests)
