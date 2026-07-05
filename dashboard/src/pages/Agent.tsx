import { useQuery } from "@tanstack/react-query";
import { Card } from "@/components/layout/Card";
import { DecisionTreeViz } from "@/components/charts/DecisionTreeViz";
import {
  getDecisionTree,
  getHypotheses,
  getTradeJournal,
} from "@/lib/api";
import { fmtTs, fmtNum, fmtUSD } from "@/lib/format";
import type {
  DecisionTreeDefinition,
  Hypothesis,
  TradeJournalEntry,
} from "@/lib/types";

export function AgentPage() {
  const treeQ = useQuery<DecisionTreeDefinition>({
    queryKey: ["agent", "decision_tree"],
    queryFn: getDecisionTree,
    staleTime: 24 * 60 * 60 * 1000, // static — cache for 24h
  });
  const hypsQ = useQuery<{ hypotheses: Hypothesis[] }>({
    queryKey: ["hypotheses", 50],
    queryFn: () => getHypotheses(50),
  });
  const journalQ = useQuery<{ entries: TradeJournalEntry[] }>({
    queryKey: ["agent", "trade_journal", 50],
    queryFn: () => getTradeJournal(50),
  });

  const tree = treeQ.data;
  const hypotheses = hypsQ.data?.hypotheses ?? [];
  const journal = journalQ.data?.entries ?? [];

  return (
    <>
      {/* Decision tree */}
      <Card
        title="Hermes Agent Decision Tree"
        extra={
          <span>
            Thresholds: SL={(tree?.thresholds.stop_loss_pct ?? -0.01) * 100}% · TP=
            {(tree?.thresholds.take_profit_pct ?? 0.025) * 100}% · Early TP=
            {(tree?.thresholds.early_profit_pct ?? 0.045) * 100}% · Flip conviction≥
            {tree?.thresholds.strong_conviction_threshold ?? 0.7}
          </span>
        }
      >
        {treeQ.isLoading && (
          <div className="text-center py-8 text-xs opacity-50">Loading decision tree…</div>
        )}
        {tree && (
          <div className="overflow-x-auto py-4">
            <DecisionTreeViz node={tree.root} />
          </div>
        )}
        <div className="alert alert-info mt-3">
          <div className="text-xs">
            <strong>How to read:</strong> Click any node with a ▾ to collapse/expand its
            branches. Thresholds shown are defaults — all configurable in{" "}
            <code className="text-primary">config/default.yaml</code>. The agent
            evaluates this tree for every existing position and every new signal.
          </div>
        </div>
      </Card>

      {/* Action legend */}
      {tree?.actions && (
        <Card title="Agent Actions">
          <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
            {tree.actions.map((a) => (
              <div key={a.id} className="flex items-center gap-2 text-sm">
                <span
                  className={`badge badge-${a.color} badge-sm`}
                  style={{ minWidth: 12, height: 12 }}
                />
                <code className="text-xs opacity-70">{a.id}</code>
                <span className="opacity-80">{a.label}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Hypotheses */}
      <Card
        title={`Hypotheses (${hypotheses.length})`}
        extra={<span>proposed → backtested → shadow → live / rejected</span>}
      >
        <p className="text-sm opacity-60 mb-4">
          Hermes generates improvement hypotheses from EOD analysis. Run{" "}
          <code className="text-primary">platform agent --eod</code> to generate new ones.
        </p>

        {hypsQ.isLoading && (
          <div className="text-center py-4 text-xs opacity-50">Loading…</div>
        )}
        {hypotheses.length === 0 && !hypsQ.isLoading && (
          <div className="alert alert-info bg-base-300 border-base-300">
            <p className="text-base-content/60 italic py-2">No hypotheses yet.</p>
          </div>
        )}
        {hypotheses.length > 0 && (
          <div className="overflow-x-auto">
            <table className="table table-zebra table-sm">
              <thead>
                <tr>
                  <th>Created</th>
                  <th>ID</th>
                  <th>Status</th>
                  <th className="text-right">Confidence</th>
                  <th>Hypothesis</th>
                  <th>Rationale</th>
                  <th>Promoted</th>
                </tr>
              </thead>
              <tbody>
                {hypotheses.map((h) => (
                  <tr key={h.hypothesis_id}>
                    <td className="font-mono text-xs opacity-60">{fmtTs(h.ts_created)}</td>
                    <td>
                      <code className="text-xs">{h.hypothesis_id.slice(0, 8)}…</code>
                    </td>
                    <td>
                      <span className={`badge badge-${statusColor(h.status)} badge-xs`}>
                        {h.status}
                      </span>
                    </td>
                    <td className="text-right">
                      {h.confidence !== undefined && h.confidence !== null
                        ? fmtNum(h.confidence, 2)
                        : "0"}
                    </td>
                    <td className="text-sm">{h.hypothesis}</td>
                    <td className="text-xs opacity-60">
                      {h.rationale ? h.rationale.slice(0, 60) : "—"}
                    </td>
                    <td className="font-mono text-xs opacity-60">
                      {h.promoted_at ? fmtTs(h.promoted_at) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Trade journal */}
      <Card title={`Trade Journal (${journal.length})`} extra={<span>with postmortems + lessons</span>}>
        {journalQ.isLoading && (
          <div className="text-center py-4 text-xs opacity-50">Loading…</div>
        )}
        {journal.length === 0 && !journalQ.isLoading && (
          <div className="alert alert-info bg-base-300 border-base-300">
            <p className="text-base-content/60 italic py-2">
              No journal entries yet. Closed trades get journal entries via the
              execution orchestrator.
            </p>
          </div>
        )}
        {journal.length > 0 && (
          <div className="overflow-x-auto">
            <table className="table table-zebra table-sm">
              <thead>
                <tr>
                  <th>Opened</th>
                  <th>Closed</th>
                  <th>Symbol</th>
                  <th>Venue</th>
                  <th>Direction</th>
                  <th>Regime</th>
                  <th className="text-right">Exit PnL</th>
                  <th className="text-right">R</th>
                  <th className="text-right">Hold</th>
                  <th>Thesis</th>
                  <th>Exit Reason</th>
                  <th>Postmortem</th>
                </tr>
              </thead>
              <tbody>
                {journal.map((j) => {
                  const pnl = j.exit_pnl ?? 0;
                  const r = j.exit_r_multiple ?? 0;
                  const holdMin = j.hold_duration_sec
                    ? Math.round(j.hold_duration_sec / 60)
                    : null;
                  return (
                    <tr key={j.journal_id}>
                      <td className="font-mono text-xs opacity-60">{fmtTs(j.opened_at)}</td>
                      <td className="font-mono text-xs opacity-60">{fmtTs(j.closed_at)}</td>
                      <td className="font-mono font-semibold">{j.symbol}</td>
                      <td className="text-xs">{j.venue}</td>
                      <td>
                        <span
                          className={`badge badge-${
                            j.direction === "long" ? "success" : "error"
                          } badge-xs`}
                        >
                          {j.direction}
                        </span>
                      </td>
                      <td className="text-xs">{j.regime_tag ?? "—"}</td>
                      <td className={`text-right font-mono ${pnl >= 0 ? "text-success" : "text-error"}`}>
                        {fmtUSD(pnl)}
                      </td>
                      <td className={`text-right font-mono ${r >= 0 ? "text-success" : "text-error"}`}>
                        {fmtNum(r, 2)}
                      </td>
                      <td className="text-right text-xs">{holdMin !== null ? `${holdMin}m` : "—"}</td>
                      <td className="text-xs opacity-70 max-w-xs truncate" title={j.entry_thesis ?? ""}>
                        {j.entry_thesis ?? "—"}
                      </td>
                      <td className="text-xs opacity-70">{j.exit_reason ?? "—"}</td>
                      <td className="text-xs opacity-70 max-w-xs truncate" title={j.postmortem ?? ""}>
                        {j.postmortem ? (
                          <span className="text-warning">📝 {j.postmortem.slice(0, 40)}…</span>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

function statusColor(status: string): string {
  if (["approved", "live", "promoted", "accepted"].includes(status)) return "success";
  if (["rejected", "failed", "error"].includes(status)) return "error";
  if (["shadow", "pending", "proposed", "backtested"].includes(status)) return "warning";
  return "ghost";
}
