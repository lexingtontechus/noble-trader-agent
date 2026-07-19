# Hardening Test Plan — Unhandled Decisions & Process Gaps

**Objective.** Harden the quant stack against actions/decisions the current code
does **not** consider. Every test case below describes a scenario the code
either mishandles today or cannot handle at all, the **required (target)**
behavior, and a concrete assertion that a future implementation must satisfy.

**Baseline (verified against code, 2026-07-17):**
- L4 `synthesize` emits `BlendedSignal` — **no approval step at L4**.
- L5 `RiskOrchestrator` runs `RiskGate` (hard rules) + `AutonomyGate.classify("enter_trade", notional, equity)`.
  - tier 1: `notional <= $5k AND notional/equity <= 2%` → **auto-approved**.
  - tier 3: `notional > $25k OR novel OR outside active hours (non-crypto)` → `human_approval_required`.
  - tier 4: structural change → hard block.
- L3 `execute` consumes `risk.decision.*`; `if not decision.approved: continue` — **it drops, it does not hold.**
- No `UserTradeIntent` path; no human-approval queue; no `approve` action; no
  portfolio-level signal ranking; no cold-start cap; no close/scale tier rules.

---

## 1. Gap register (refined)

### GA — Cold-start autonomous cap far too loose for a new user
- **Current:** `tier1_max_notional=$5k`, `tier1_max_position_pct=2%`, applied
  identically to **all** users. For a new user (equity $100k, 0 trades) the
  binding cap is `2% = $2k/trade`, and there is **no limit on how many
  positions** can be opened. 25–50 buy signals → up to $50k–$100k auto-executed
  on day one with zero track record.
- **Risk:** Catastrophic over-deployment of an unproven account.
- **Target rule:** **Cold-start mode** for users with no proven track record
  (tenure < `cold_start_days` OR closed_trades < `cold_start_exit_n`). Tighter
  per-trade cap **and** a position-count + total-exposure budget.
- **Config knobs (proposed, under `autonomy.cold_start`):**
  ```
  cold_start:
    enabled: true
    tier1_max_notional: 500        # vs 5000 normal
    tier1_max_position_pct: 0.005   # vs 0.02 normal
    max_new_positions: 3           # hard cap on concurrent new positions
    max_new_exposure_pct: 0.05      # total new exposure <= 5% of equity
    exit_after_n_trades: 20        # leave cold-start after 20 closed trades
  ```

### GB — No portfolio-level signal selection / ranking (agent approves all)
- **Current:** every `BlendedSignal` that passes risk + tier-1 is approved and
  executed. No cross-signal ranking, no position-count budget, no
  diversification penalty. 25–50 buys → all admitted.
- **Risk:** Over-concentration, no PM discipline, poor diversification.
- **Target rule:** A **selection layer (L4.5)** ranks all candidate signals for
  the cycle by a configurable score and admits **top N** (or those above a
  threshold). Excess signals are **deferred** (re-queued for next cycle) or
  **rejected** with reason `selection_budget_exhausted`. Score weights pattern
  confidence, expected alpha, R:R, regime alignment, and a correlation penalty.
- **Config knobs (proposed, under `portfolio.selection`):**
  ```
  selection:
    max_new_positions_per_cycle: 3
    policy: top_n                  # top_n | score_threshold | max_correlated
    score_weights:
      pattern_confidence: 0.30
      expected_entry_alpha_bps: 0.25
      reward_risk: 0.20
      regime_alignment: 0.15
      diversification: 0.10
    max_correlated_exposure: 0.20  # cap on same-sector/exchange exposure
  ```

### GC — User-initiated trade with no signal: analysis/sim branch
- **Current:** No path (§15.2 #3). User BUY/SELL with no `signal_id` cannot reach L5.
- **Target rule:** A **user intent** spawns an **analysis + simulation
  sub-process** (reuse `RenkoSimulationEngine` / `optimizer`) that evaluates the
  trade and emits a `UserIntentAssessment`: either **reject** (with rationale:
  negative expectancy, no pattern support, R:R < min) or **approve** (synthesized
  signal + sim confidence). The assessment then flows through `RiskGate` +
  `AutonomyGate` → `RiskDecision` → L3. User sees the rationale either way.
- **Config knobs (proposed, under `user_intent`):**
  ```
  user_intent:
    require_sim: true
    sim_min_trades: 200
    auto_reject_if_neg_expectancy: true
    max_sim_latency_sec: 30
  ```

### GD — Unsupported venue (M4): reject at L4 qualify  ✅ confirmed correct
- **Current:** `symbol_key.qualify_symbol()` returns no `Venue` for an
  unsupported exchange (e.g. SSE small-cap). Today the signal may still flow to L5.
- **Target rule (tighten):** Explicit **reject at L4** before L5/L3, reason
  `unsupported_venue`. Never publishes a `risk.decision`, never reaches execution.
  Applies to both Redis signals (M4) and user-intent discovery (§15 D3).
- **Assertion:** unsupported-venue heartbeat → 0 `risk.decision.*` published, 0 orders.

### GE — Human-approval queue + approve action missing
- **Current:** tier-3 → `human_approval_required` → L5 forces `approved=False` →
  L3 `continue` (drops and forgets). No queue, no `approve` endpoint, no timeout.
- **Target rule:** Persist pending decisions in a **pending-approval queue**
  (DuckDB `pending_decisions`). Expose `POST /api/approve/{decision_id}` (web)
  and `platform approve {id}` (CLI). On approve, L3 re-evaluates and executes.
  **Timeout** (`approve_timeout_hours`, default 4) → auto-expiry/reject.
- **Assertion:** tier-3 decision appears in queue, not executed; approve →
  executes; timeout → rejected.

### GF — Duplicate decision processing at L3
- **Current:** L3 processes every `risk.decision.*` message; if republished
  (redelivery / restart replay) it can double-execute.
- **Target rule:** Dedupe by `decision_id` (seen-set persisted in DuckDB
  `executed_decisions`). Same id → no second order.
- **Assertion:** same decision published twice → exactly one order.

### GG — close_trade / scale / reduce autonomy undefined
- **Current:** `AutonomyGate.classify` handles `enter_trade` but close/scale rules
  are implicit; a user "close 50%" and "add to winner" are not distinguished.
- **Target rule:** Explicit tiers:
  - `close_trade` (reduce risk / stop) → tier 1 (auto), unless it increases net
    exposure (then treated as enter).
  - `scale_in` / `add_to_winner` → tier 3 (human approval), sized like enter.
- **Assertion:** user close 50% → auto; user add-to-position → held for approval.

---

## 2. Structured test cases

| TC | Gap | Scenario | Preconditions | Action | Current (buggy) | Required (target) | Assertion | Pri |
|----|-----|----------|---------------|--------|-----------------|-------------------|-----------|-----|
| TC-01 | GA+GB | New user, 30 buy signals | equity $100k, 0 trades, cold-start on | Redis delivers 30 buys | all tier-1 → up to $60k auto-executed | cold-start caps + top-3 selection → ≤3 positions, ≤5% exposure | executed positions ≤3; exposure ≤5% | **P0** |
| TC-02 | GA | New user, 1 buy > cold-start cap | cold-start `tier1_max_notional=500` | 1 buy $3k | tier-1 auto (≤$2k normal) → executes $2k | $3k > 500 → tier-3 hold | decision `human_approval_required`, not executed | **P0** |
| TC-03 | GB | Concentration: 5 BTC-correlated buys | normal user | 5 BTC/ETH/SOL buys | all 5 execute (no diversification check) | selection penalizes correlation → top-3 by diversification | ≤3 execute; same-sector exposure ≤ `max_correlated_exposure` | **P0** |
| TC-04 | GD | Redis signal, unsupported venue | — | heartbeat `SSE:000001` | may reach L5/L3 | reject at L4 qualify | 0 risk.decision, 0 orders, logged `unsupported_venue` | **P0** |
| TC-05 | GC | User BUY no signal, tradeable | user intent BTC/USD | "BUY BTC/USD" | no path → ignored/error | sim branch → assessment → approve at L3 | order created OR explicit reject w/ rationale | **P1** |
| TC-06 | GC | User BUY no signal, sim rejects | user intent obscure coin | "BUY XYZ" | no path | sim runs → negative expectancy → reject | no order; user sees `sim_reject:neg_expectancy` | **P1** |
| TC-07 | GE | tier-3 hold → approve | 1 buy $30k | signal arrives | dropped, forgotten | queued pending | in `pending_decisions`, not executed | **P1** |
| TC-08 | GE | approve action | TC-07 queued | `platform approve {id}` | n/a | L3 re-evaluates → executes | exactly 1 order; removed from queue | **P1** |
| TC-09 | GE | approve timeout | TC-07 queued | wait > `approve_timeout_hours` | n/a | auto-expiry | decision `rejected:approval_expired` | **P1** |
| TC-10 | GF | duplicate decision | 1 approved buy | publish same `decision_id` twice | possible double-exec | dedupe by id | exactly 1 order | **P2** |
| TC-11 | GG | user close 50% | open position | "CLOSE 50% BTC" | implicit/unclear | tier-1 auto close | order closes 50%, no human step | **P2** |
| TC-12 | GG | user add to winner | open BTC position | "ADD $5k BTC" | implicit | tier-3 (scale_in) | held for approval | **P2** |
| TC-13 | GA | cold-start exit | 20 closed trades, positive expectancy | new buy | still cold-start caps | exits cold-start → normal tier-1 | trade uses normal $5k/2% cap | **P1** |
| TC-14 | GB | selection policy = score_threshold | normal user, 30 buys | signals arrive | all execute | only score ≥ threshold admitted | count admitted == those ≥ threshold | **P1** |
| TC-15 | GD | user discovery unsupported venue | §15 discovery | pick SSE stock → BUY | no path | qualify rejects at discover | "TRADE" disabled / rejected w/ reason | **P0** |
| TC-16 | GA+GE | new user, 1 large + 1 small buy | cold-start | 2 buys ($3k, $400) | $3k tier-1 (bug), $400 tier-1 | $3k tier-3 hold, $400 tier-1 exec | 1 executed, 1 queued | **P0** |
| TC-17 | Risk | kill switch + pending | TC-07 queued, kill switch flips | n/a | n/a | pending stays queued, not executed while kill active | 0 orders while kill active | **P1** |
| TC-18 | GC | user SELL no position | no BTC holding | "SELL BTC" | no path | sim/assessment → reject (nothing to close) | no order; `reject:nothing_to_close` | **P1** |
| TC-19 | GB | deferred re-queue | TC-01 excess 27 buys | next cycle | dropped | deferred signals re-ranked next cycle | previously-deferred can be admitted if budget frees | **P2** |
| TC-20 | GA | normal user, 30 buys (no cold-start) | tenure > cold-start | 30 buys | all tier-1 execute | top-N by selection (normal caps) | ≤ `max_new_positions_per_cycle` execute | **P1** |

**Priority legend:** P0 = catastrophic / must-fix before any new-user onboarding;
P1 = core handleability (user intent + approval queue); P2 = robustness.

---

## 3. Implementation sequencing (recommended)

1. **P0 first** — GA (cold-start caps) + GB (selection layer) + GD (reject at L4).
   These three alone stop the "new user auto-deploys $100k on 50 signals" failure.
2. **P1** — GC (user-intent sim branch) + GE (approve queue) + GG close/scale tiers.
   Makes user-driven trading + human oversight actually work.
3. **P2** — GF (dedupe) + deferred re-queue (TC-19). Robustness/polish.

---

## 4. Config knobs summary (user-tunable, proposed)

| Knob | Block | Default (new user) | Normal user | Effect |
|------|-------|--------------------|-------------|--------|
| `tier1_max_notional` | `autonomy` | 100 (cold) / 5000 | 5000 | per-trade auto cap |
| `tier1_max_position_pct` | `autonomy` | 0.002 (cold) / 0.02 | 0.02 | per-trade % cap |
| `cold_start.max_new_positions` | `autonomy.cold_start` | 3 | n/a | concurrent new pos cap |
| `cold_start.max_new_exposure_pct` | `autonomy.cold_start` | 0.05 | n/a | total new exposure cap |
| `cold_start.exit_after_n_trades` | `autonomy.cold_start` | 20 | n/a | leave cold-start |
| `selection.max_new_positions_per_cycle` | `portfolio.selection` | 3 | 3 | top-N admits/cycle |
| `selection.policy` | `portfolio.selection` | top_n | top_n | ranking policy |
| `selection.score_weights.*` | `portfolio.selection` | see §1 GB | same | ranking weights |
| `user_intent.require_sim` | `user_intent` | true | true | sim gate on user trades |
| `approve_timeout_hours` | `autonomy` | 4 | 4 | pending expiry |

---

## 5. Open questions for the user

- **Cold-start exit criteria:** trade count (20) vs time (30d) vs equity-proof
  (positive expectancy)? Recommend count + expectancy.
- **Selection default N:** 3 per cycle reasonable for a new user? Or 1?
- **User-intent sim latency:** 30s budget acceptable for an interactive BUY, or
  should it be async (user gets "analyzing…" then a notification)?
- **Deferred signals:** re-queue across cycles (TC-19) or drop after 1 cycle?

---

## 6. Implementation Status

**P0 — DONE (2026-07-18).** GA (cold-start caps + budget + auto-exit), GB
(selection layer top-N=3, drop excess), GD (unsupported-venue reject at L5) implemented
+ tested. `pytest tests/test_hardening_p0.py` → 10 passed. Changelog session 8 recorded.

- `config/default.yaml`: `autonomy.cold_start` block (tier1_max_notional=100,
  tier1_max_position_pct=0.002, max_new_positions=3, max_new_exposure_pct=0.05,
  exit_after_n_trades=20, exit_min_expectancy_bps=0) + `portfolio.selection` block
  (max_new_positions_per_cycle=3, cycle_window_sec=300, score_weights).
- `src/hermes/portfolio/autonomy_gate.py`: cold-start params + position/exposure budget
  enforcement in `classify()`.
- `src/hermes/portfolio/selection.py`: new `SelectionLayer` (rank + admit top-N, drop excess).
- `src/hermes/portfolio/orchestrator.py`: `_is_supported_venue` (GD), `_check_cold_start_exit`
  (GA exit), GD+GB calls inside `evaluate_signal`.
- `src/hermes/core/config.py`: `PortfolioConfig.selection` field added.

**P1 — DONE (2026-07-18).** GC (user-intent sim branch, mandatory + non-configurable, routes
through L5) + GE (human-approval queue via msg channel: store + alert + approve CLI) implemented
+ tested. `pytest tests/test_hardening_p0.py tests/test_hardening_p1.py` → 15 passed.
Changelog session 9 recorded. New CLI: `platform trade`, `platform approve`, `platform pending`.
Migration 013 adds `pending_decisions`. RiskDecision gained `requires_human_approval` + `status`.

**P2 — DONE (2026-07-18).** GF (duplicate-decision idempotency at L3) implemented + tested.
`pytest tests/test_hardening_p0.py tests/test_hardening_p1.py tests/test_hardening_p2.py`
→ 17 passed. Changelog session 10 recorded. Deferred re-queue (TC-19) confirmed = DROP
after 1 cycle (already in GB from P0; no re-queue code needed).

All P0/P1/P2 hardening gaps closed.

## 7. Open Questions — RESOLVED (user decisions 2026-07-18)

- **Cold-start vs existing:** existing portfolios are NOT cold-start (normal tier-1
  $5k/2%); business as usual, wait for Redis signal. No portfolio-optimization sweep.
- **Cold-start exit:** count (`exit_after_n_trades=20` closed) + positive expectancy
  (realized PnL > 0). Both required.
- **Selection default N:** 3 per cycle (configurable).
- **User-intent sim latency:** 60s hard gate (not HFT). GC sim is MANDATORY +
  non-configurable (`require_sim=true`) — a configurable skip would break pricing/
  monitor/backtest processes.
- **Deferred signals:** DROP after 1 cycle (do not re-queue).
- **GE channel:** human-approval via msg channel (Discord/Telegram) — pending tier-3
  decision posts an alert with an `approve` action; `platform approve {id}` or a
  Discord reaction executes. (P1, not yet built.)

