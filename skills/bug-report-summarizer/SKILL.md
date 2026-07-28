---
name: bug-report-summarizer
slug: bug-report-summarizer
version: 1.0.0
description: >
  Generates a 1-2 sentence rationale per §2.2 + a structured GitHub Issue
  body for each `noble bug` invocation. Pulls recent audit_log entries,
  circuit_breaker_events, risk_decisions, and recent log lines to
  construct a context-rich bug report that an operator can file with
  minimal editing.
---

# Bug Report Summarizer Skill

> **Phase 1A v10 — scoped contract.** This skill is a forward-looking
> contract for Phase 1B/2/3. It is NOT yet implemented. The workflow
> below uses the v10 `skill_invoker` callable seam (constructor-injected
> by the agent runtime). When this skill is implemented, the caller
> service (mirroring `TradeJournal` in `src/hermes/ops/trade_journal.py`)
> will own the SELECT/INSERT/UPDATE plumbing; the skill_invoker owns the
> inference. See `LLM-INTEGRATION-STRATEGY.md` and the canonical
> `skills/trade_journal/SKILL.md` exemplar for the v10 contract.

## When to Use

Invoked by `BugReport.generate()` when the operator runs `noble bug`.
The skill reads recent system state (audit log, breaker events, risk
decisions, recent log lines) and generates a structured GitHub Issue
body. The operator reviews, edits if needed, and the issue is filed
via the GitHub API.

## Architecture

Reads the last N hours (default 24) of `audit_log` + any recent
`circuit_breaker_events` + recent `risk_decisions` + the tail of the
recent log files. Produces:

1. `rationale` — 1-2 sentence hook per §2.2 ("what's the user-observable
   symptom, in plain English"). Surfaced as the GitHub Issue title.
2. `issue_body` — a structured GitHub Issue body in Markdown:
   - **Summary**: 1 paragraph restating the rationale.
   - **Steps to reproduce** / **Observed behavior** / **Expected behavior**:
     inferred from the audit log + log lines.
   - **Recent system state**: breaker events, risk decisions, key audit
     log entries from the window.
   - **Config hash**: the active `config_hash` at the time of the report.
   - **Log excerpts**: the most relevant WARN/ERROR lines (redacted for
     secrets per `security_monitor._redact_sensitive_data`).
3. `severity` — `low` / `medium` / `high` / `critical` based on breaker
   count + error density.

## Scope

This skill ONLY:

- Generates a bug report from **recent** system state.
- Writes to `bug_report_summaries` (new table, FK → `bug_report_id`).
- Reads from `audit_log`, `circuit_breaker_events`, `risk_decisions`,
  recent log files.

This skill NEVER:

- Files the issue automatically. The operator reviews + files.
- Modifies any of the underlying records it reads.
- Touches live trading, signal generation, or risk decisions.
- Makes any external API call other than the agent's own inference
  router (which the agent owns and operates).
- Includes raw secrets. All log excerpts are redacted via
  `security_monitor._redact_sensitive_data` before being included.

## Core Rules

1. **`rationale` is 1-2 sentences.** Plain English. User-observable
   symptom only — no internal module names. Example: "Risk gate has
   rejected every signal in the last 2 hours with reason
   'var_breach_post_trade'."
2. **`issue_body` is structured Markdown.** Follow the GitHub Issue
   template (Summary / Steps / Observed / Expected / System state /
   Config / Logs).
3. **Redact everything sensitive.** API keys, session tokens, IP
   addresses, account numbers — all redacted via
   `security_monitor._redact_sensitive_data` before inclusion. If in
   doubt, redact.
4. **`severity` is one of** `low` / `medium` / `high` / `critical`. Be
   conservative — `critical` is reserved for issues that block trading.
5. **Cite timestamps.** Every quoted log line / audit entry must include
   its timestamp.
6. **Never invent steps to reproduce.** If the audit log doesn't show
   them, say "Unknown — see log excerpts".
7. **No PII, no account numbers.**

## Workflow

```
1. Operator runs: noble bug
   → BugReport.generate() collects recent state.

2. Build payload:
     payload = {
       "window_hours":      24,
       "audit_log":         <recent audit_log rows>,
       "breaker_events":    <recent circuit_breaker_events>,
       "risk_decisions":    <recent risk_decisions>,
       "log_excerpts":      <redacted WARN/ERROR lines from recent logs>,
       "config_hash":       <active config_hash>,
       "operator_note":     <optional free-text from --note flag>,
     }

3. Call skill_invoker(skills/bug-report-summarizer/SKILL.md, payload) → result
   (skill_invoker is the agent's own inference router, injected by the
   caller — the service class constructor accepts it as a kwarg; the
   CLI raises a clear RuntimeError if it's None)

4. On success:
     INSERT INTO bug_report_summaries (
       bug_report_id, rationale_llm, issue_body_llm, severity,
       summary_status, generated_at
     ) VALUES (
       ?, ?, ?, ?,
       'generated', now()
     )

     # Print the issue body for the operator to review + file
     print(result["issue_body"])
     print("\n-- File via: noble bug --file-issue", bug_report_id)

5. On failure (skill_invoker raises or returns empty):
     INSERT INTO bug_report_summaries (
       bug_report_id, summary_status, generated_at
     ) VALUES (
       ?, 'llm_unavailable', now()
     )
```

## Output Schema

```json
{
  "rationale":  "<1-2 sentence plain-English symptom>",
  "issue_body": "<structured Markdown GitHub Issue body>",
  "severity":   "low|medium|high|critical"
}
```

## References

(future)
- `references/issue_template.md` — the GitHub Issue template structure
- `references/redaction_rules.md` — what gets redacted + how

## Examples

(future)
