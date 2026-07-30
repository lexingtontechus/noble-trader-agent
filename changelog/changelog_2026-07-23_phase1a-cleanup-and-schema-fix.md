# Changelog — PHASE-1A-CLEANUP + SCHEMA-MISMATCH-FIX

**Date/Time:** 2026-07-23 (PDT)
**Agent:** main (Super Z)
**Scope:** Two coordinated follow-ups to the Phase 1A `trade_journal` rollout,
flagged as known issues in the Phase 1B Stage Summary and the P1A-CLEANUP-PASS
worklog entry respectively:

1. **P1A cleanup pass** — apply the same SQL three-valued-logic fix to
   `TradeJournal._select_pending()` that Phase 1B's smoke test caught + fixed
   in `SignalExplainer._select_pending()`, and add a Phase 1A smoke test
   exercising the NULL-row selection path.
2. **Schema mismatch fix** — `TradeJournal._select_pending()` JOINs
   `pnl_realized pr ON tsb.signal_id = pr.signal_id` and SELECTs
   `pr.exit_reason`, but migration 006's `pnl_realized` table has neither
   column. Add both columns via a new migration, thread them through the
   service layer + orchestrator.

Same clean approach as Phase 1A v10 / Phase 1B end-to-end: minimal,
mechanical, guarded by smoke tests + a standalone migration verification.

---

## 1. P1A cleanup pass — SQL three-valued-logic bug fix

### 1.1 The bug

`TradeJournal._select_pending()` (in `src/hermes/ops/trade_journal.py`) had
a redundant `AND tp.postmortem_status NOT IN ('reviewed', 'skipped')`
clause in its WHERE clause. The `status_clause` portion already enumerates
the statuses we DO want via OR — `IS NULL OR = 'llm_unavailable' OR =
'generated'` — so the `NOT IN` clause was logically redundant.

But worse than redundant: SQL three-valued logic makes
`NULL NOT IN ('reviewed', 'skipped')` evaluate to **NULL** (not TRUE),
which silently filtered out the very NULL-status rows the nightly cron is
supposed to process. Any `trade_postmortem` row with
`postmortem_status IS NULL` was being dropped by the redundant clause.

The identical bug existed in `SignalExplainer._select_pending()` and was
caught + fixed during Phase 1B smoke testing. The Phase 1B Stage Summary
flagged the same bug in Phase 1A as a known follow-up; this changelog
entry resolves it.

### 1.2 The fix

Single clause removed from `TradeJournal._select_pending()`. A 7-line
explanatory comment block added above the `status_clause` construction:

```python
# DO NOT add a redundant `AND postmortem_status NOT IN
# ('reviewed', 'skipped')` sibling clause — that pattern was
# removed in the Phase 1A cleanup pass because SQL three-valued
# logic makes `NULL NOT IN (...)` evaluate to NULL (not TRUE),
# which silently filters out the very NULL rows we want to
# process. The status_clause enumeration alone is sufficient.
```

The fix mirrors the Phase 1B fix end-to-end: same clause removed, same
explanatory comment pattern, same lifecycle-state matrix, same retry
contract assertions.

### 1.3 The smoke test — `scripts/smoke_test_phase1a_trade_journal.py`

New 488-line smoke test mirroring `smoke_test_phase1b_signal_explainer.py`
end-to-end. Builds an in-memory DuckDB at `/tmp/p1a_smoke.duckdb`, creates
the minimum schema (`trade_signals_blended` + `pnl_realized` via migration
006 + migration 021 + `trade_postmortem` via migration 019), monkeypatches
`hermes.db.migrate.get_duckdb_path`, seeds 4 signals covering all 4
lifecycle states (SIG-NULL / SIG-REVIEWED / SIG-SKIPPED / SIG-FAILED), and
runs 8 tests:

| # | Test | Asserts |
|---|------|---------|
| 1 | `generate_postmortem_for_day` selects BOTH NULL AND llm_unavailable rows | Nightly cron picks up both never-processed + previously-failed rows per SKILL.md retry contract; payload shape verified (signal_id / symbol / direction / nt_entry_price / nt_effective_kelly / meta_regime / net_pnl / r_multiple / exit_reason) |
| 2 | `backfill(retry_failed=False)` selects zero rows when all rows are generated/reviewed/skipped | Without `--retry-failed`, llm_unavailable rows are protected |
| 3 | `backfill(retry_failed=True)` retries SIG-FAILED | Resets to 'llm_unavailable' first, then regenerates to 'generated' |
| 4 | `generate_postmortem_for_day(force=True)` regenerates SIG-NULL + SIG-FAILED | Still protects SIG-REVIEWED and SIG-SKIPPED |
| 5 | Failure path — `skill_invoker` raises RuntimeError | Row marked 'llm_unavailable' (retryable) |
| 6 | Empty result path — skill returns `{"postmortem_llm": ""}` | Row marked 'llm_unavailable' (postmortem_llm REQUIRED) |
| 7 | None `skill_invoker` | Clear RuntimeError raised |
| 8 | **REGRESSION GUARD** for the SQL three-valued logic bug | Resets table to clean state, calls `_select_pending(retry_failed=False, force=False)` directly, asserts result set is exactly `["SIG-NULL"]`. If SIG-NULL is missing, the redundant NOT IN clause has returned |

Result: **8/8 PASS**. Phase 1B smoke test re-verified: **7/7 PASS** (no
regressions in adjacent code).

### 1.4 Bug found + verified during smoke testing

To verify Test 8 actually catches the bug, the buggy clause was temporarily
restored and the smoke test re-run: Test 8 failed with
`Expected only SIG-NULL (the NULL-status row) to be selected, got []`.
Confirmed the regression guard works. (Buggy clause then re-removed to
leave the fix in place.)

---

## 2. Schema mismatch fix — migration 021

### 2.1 The mismatch

`TradeJournal._select_pending()` JOINs `pnl_realized pr ON
tsb.signal_id = pr.signal_id` and SELECTs `pr.exit_reason` +
`pr.config_hash`. Migration 006's `pnl_realized` table has neither
`signal_id` nor `exit_reason` columns. Production was previously broken
at this query — any nightly `noble journal generate` / `noble journal
backfill` call would have failed with
`Catalog Error: Column "signal_id" not found` at the JOIN.

The P1A-CLEANUP-PASS worklog flagged this as a known follow-up. This
changelog entry resolves it.

### 2.2 The fix — 4 coordinated code changes

#### 2.2.1 New migration `src/hermes/db/migrations/021_pnl_realized_signal_id_exit_reason.sql` (41 lines)

```sql
ALTER TABLE pnl_realized ADD COLUMN IF NOT EXISTS signal_id   VARCHAR;
ALTER TABLE pnl_realized ADD COLUMN IF NOT EXISTS exit_reason VARCHAR;

CREATE INDEX IF NOT EXISTS idx_pnl_realized_signal
    ON pnl_realized (signal_id);

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (21, 'Phase 1A cleanup: pnl_realized.signal_id + exit_reason columns');
```

Both columns are nullable. Existing `pnl_realized` rows (from before this
migration) keep NULL — they can be backfilled by joining through
`trade_journal.trade_id`, but no backfill is required for forward
operation. New rows get the values populated by
`ExecutionOrchestrator._on_position_closed()`.

Mirrors the migration 011 / 014 ALTER TABLE pattern for `pnl_realized`.
Idempotent: `ADD COLUMN IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS` —
safe to re-run on dev/staging/tenant deployments.

#### 2.2.2 `src/hermes/analytics/pnl_service.py` — 3 coordinated edits

1. **`RealizedPnL` dataclass** — added `signal_id: str | None = None` +
   `exit_reason: str | None = None` between `regime_at_close` and the
   components block. 7-line comment block explains the Phase 1A cleanup
   context + the JOIN they enable + why both are nullable.

2. **`record_realized_pnl()` signature** — added `signal_id: str | None =
   None` + `exit_reason: str | None = None` keyword arguments between
   `config_hash` and the Phase C Bayesian alpha fields. Comment block
   explains both values come from the orchestrator's `_on_position_closed`
   scope (signal_id from the `_position_signals` map, exit_reason from
   `decision.action.value`).

3. **`_write_realized()` INSERT** — added `signal_id, exit_reason,` to
   the column list (between `regime_at_close` and `gross_pnl`) +
   corresponding `pnl.signal_id, pnl.exit_reason,` to the params list.
   Placeholder count went from 26 to 28.

#### 2.2.3 `src/hermes/execution/orchestrator.py` — 1 edit at the call site

In `_on_position_closed()` (line ~376), added two kwargs to the
`record_realized_pnl()` call:

```python
signal_id=signal_id or None,         # coerces "" → None (paper/research trades)
exit_reason=decision.action.value,   # e.g. 'tp_hit', 'sl_hit', 'manual', 'regime_change'
```

Both values were already in scope at the call site — `signal_id` was
already computed for the BayesianAlpha hook + the legacy `trade_journal`
write, and `decision.action.value` was already passed to
`position.remove_position()` and used for the `_write_v1_postmortem()`
call. The fix is purely a pass-through — no new computation needed in
the orchestrator.

The `signal_id or None` coercion handles the empty-string fallback from
`_position_signals.get()` (which returns `""` if the position wasn't
opened from a signal — e.g., paper / research trades), keeping the
column NULL rather than `''`.

#### 2.2.4 `scripts/smoke_test_phase1a_trade_journal.py` — replaced workaround with canonical schema

The smoke test had a workaround: it manually created `pnl_realized` with
`signal_id` + `exit_reason` columns hardcoded, with a "the cleanup pass
does NOT fix this latent issue (out of scope)" disclaimer. Now that the
schema mismatch is fixed, the workaround is replaced with:

1. Create `pnl_realized` via the canonical migration 006 base schema
   (no `signal_id` / `exit_reason`).
2. Apply migration 021 against it via `conn.execute(migration_021_path.read_text())`.

This is the same pattern the Phase 1B smoke test uses for migration 020 —
the smoke test now mirrors the production ALTER chain end-to-end.

The `pnl_realized` seed INSERT was also updated to populate the NOT NULL
columns that were previously omitted (`net_pnl_bps`, `risk_amount`,
`n_fills`, `strategy_id`). The seed row for `SIG-NULL` now has all 22
columns populated, matching the production `_write_realized()` INSERT
shape.

### 2.3 Standalone migration 021 verification

In addition to the smoke test, a standalone migration 021 verification
was run against a fresh in-memory DuckDB (7 tests):

| # | Test | Asserts |
|---|------|---------|
| 1 | Migration applies cleanly on the 006+011+014 base | No errors |
| 2 | Columns exist after migration | `signal_id` + `exit_reason` present in `pnl_realized` |
| 3 | Index exists | `idx_pnl_realized_signal` present |
| 4 | `schema_version` row inserted | `version=21` row present |
| 5 | Idempotent re-run | Re-applying migration produces no errors + no duplicate `schema_version` rows |
| 6 | `_write_realized()` INSERT shape works | INSERT with all 28 columns succeeds |
| 7 | JOIN end-to-end | `trade_signals_blended.signal_id = pnl_realized.signal_id` JOIN works |

All 7 PASS.

---

## 3. Anomalies / judgment calls

### 3.1 Migration 019 partial-skip in smoke test

Migration 019 includes `ALTER TABLE trade_journal DROP COLUMN IF EXISTS ...`
statements against the legacy `trade_journal` table, which the smoke DB
doesn't create. DuckDB raises `Catalog Error: Table with name trade_journal
does not exist!` when executing the migration. The smoke test catches
this exception, logs a partial-skip notice, and manually creates the
`trade_postmortem` table + indexes (the only part of migration 019 the
smoke test needs). This is smoke-test-only — production applies migration
019 against a real DB that already has the `trade_journal` table from
`schema.sql`.

### 3.2 `signal_id or None` coercion in orchestrator

The `_position_signals` dict lookup returns `""` on miss (not `None`), so
we coerce to `None` at the call site. Without this, paper/research trades
that weren't opened from a blended signal would write `''` (empty string)
to `pnl_realized.signal_id` instead of `NULL`. The `LEFT JOIN` in
`TradeJournal._select_pending()` would still work either way, but `NULL`
is the canonical "no upstream signal" marker and keeps `IS NULL` queries
idiomatic.

### 3.3 No backfill of pre-021 rows

Existing `pnl_realized` rows (from before this migration) keep NULL
`signal_id` + NULL `exit_reason`. They CAN be backfilled by joining
through `trade_journal.trade_id` (the legacy table still has
`exit_reason`), but no backfill is required for forward operation. New
rows get the values populated by the orchestrator. Flagged as an optional
follow-up if the operator wants historical PnL rows to participate in
postmortem backfill — but the `LEFT JOIN` already handles missing data
gracefully (the postmortem skill payload simply has `net_pnl=NULL` for
those rows, which the skill file already handles).

### 3.4 No FK constraint

`pnl_realized.signal_id` is NOT a foreign key to
`trade_signals_blended.signal_id`. This is intentional — matches the
existing convention on `orders.signal_id` (migration 005 line 9) and
`risk_decisions.signal_id` (schema.sql line 196), neither of which has
an FK constraint. DuckDB FK enforcement is best-effort and the codebase
doesn't use it elsewhere; the JOIN is the source of truth.

### 3.5 No update to `signals/synthesizer.py` or `agent/bayesian_alpha.py`

Those files read `pnl_realized` for analytics / Bayesian alpha tracking,
but neither JOINs on `signal_id` (they JOIN on `trade_id`). Their
behavior is unchanged.

### 3.6 No production-data verification

This cleanup pass is verified entirely by the smoke test + standalone
migration verification against in-memory DuckDB. The P1A fix is a single
SQL clause removal with no semantic side effects (the `status_clause`
already enumerates the statuses we want; removing the redundant `NOT IN`
only ADDS NULL rows back to the result set, never removes anything). No
production data migration is required; existing rows in `trade_postmortem`
with `postmortem_status IS NULL` will simply start getting picked up by
the nightly cron on the next run (which is the intended behavior — those
rows were silently being skipped before the fix).

---

## 4. Deliverables

### Code changes (4 files)

| File | Change |
|------|--------|
| `src/hermes/db/migrations/021_pnl_realized_signal_id_exit_reason.sql` | New file, 41 lines (ALTER TABLE x2 + CREATE INDEX + schema_version) |
| `src/hermes/analytics/pnl_service.py` | 3 edits — `RealizedPnL` dataclass + `record_realized_pnl()` signature + `_write_realized()` INSERT (file 554 → 569 lines) |
| `src/hermes/execution/orchestrator.py` | 1 edit at the `record_realized_pnl()` call site, +14 lines including the comment block |
| `src/hermes/ops/trade_journal.py` | 1 clause removed + 7-line explanatory comment block (file 417 → 425 lines) |

### Smoke tests

| File | Tests | Result |
|------|-------|--------|
| `scripts/smoke_test_phase1a_trade_journal.py` | 8 (incl. Test 8 regression guard for the NULL-row selection bug) | 8/8 PASS |
| `scripts/smoke_test_phase1b_signal_explainer.py` | 7 (re-run as regression check) | 7/7 PASS |

### Documentation updates

| File | Update |
|------|--------|
| `skills/README.md` | Status block bumped; Phasing table Phase 1A row annotated with cleanup pass + schema fix note; Cross-references block added Migration 021 entry + smoke tests entry |
| `download/LLM-INTEGRATION-STRATEGY.md` | Status block bumped; Phase 1A Definition of Done checklist gained 3 new `[x]` items (cleanup pass SQL fix, cleanup pass smoke test, schema mismatch fix via migration 021) |

---

## 5. Known follow-ups (operator decision, separate work items)

### 5.1 Optional historical backfill

Existing `pnl_realized` rows have NULL `signal_id` + NULL `exit_reason`.
If the operator wants historical PnL rows to participate in postmortem
backfill, a one-shot SQL would backfill them:

```sql
UPDATE pnl_realized pr
SET signal_id = (SELECT signal_id FROM orders WHERE trade_id = pr.trade_id LIMIT 1),
    exit_reason = (SELECT exit_reason FROM trade_journal WHERE trade_id = pr.trade_id LIMIT 1)
WHERE signal_id IS NULL;
```

Not required for forward operation; flagged as optional.

### 5.2 Remaining scoped SKILL.md files

The 9 remaining scoped SKILL.md files (Phases 2 + 3 + the 7 standalone
skills) still have the "Phase 1A v10 — scoped contract" note block at
top of body. When their phases are promoted, they should be rewritten to
live contracts following the same pattern as `trade_journal` +
`signal-explainer` (drop the note, fill in `references/*` + `examples/*`,
add Cron model + Retry contract + Workflow sections matching the live
caller service).

### 5.3 Downstream subscriber work (unchanged from prior changelogs)

The 3 unchecked items in the Phase 1A + 1B Definition of Done checklists
remain: dashboard `/api/signals` JOIN to `signal_explanations`,
`nobletradingapp` tooltip reads `rationale` field, AlertManager alert
evaluation query JOINs `signal_explanations`. These are downstream work
in `nobletradingapp` + the Hermes web app + AlertManager and are
unchanged by this changelog.

---

## 6. Next steps (operator decision)

1. **Deploy migration 021 to production** — idempotent; safe to re-run;
   existing rows keep NULL, new rows get populated. No downtime.
2. **Deploy the P1A cleanup fix** — single-line removal; no migration,
   no downtime. The next nightly cron run will start picking up
   NULL-status `trade_postmortem` rows that were previously silently
   skipped.
3. **Review the optional historical backfill SQL** in §5.1 if historical
   PnL rows should participate in postmortem backfill.
4. **When Phase 2 (narrative-classifier) is promoted**, follow the same
   clean approach — caller service mirroring `TradeJournal` /
   `SignalExplainer`, CLI under `noble`, migration creating the new
   table, SKILL.md rewrite, smoke test mirroring the Phase 1A + 1B
   pattern.
