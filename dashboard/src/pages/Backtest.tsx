import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card } from "@/components/layout/Card";
import { StatsGrid } from "@/components/charts/StatsGrid";
import { EquityCurve } from "@/components/charts/EquityCurve";
import {
  getBacktestRuns,
  getBacktestRunDetail,
} from "@/lib/api";
import { fmtTs, fmtUSD, fmtPct, fmtNum } from "@/lib/format";
import type { BacktestRun, BacktestRunDetail } from "@/lib/types";

export function BacktestPage() {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  const runsQ = useQuery<{ runs: BacktestRun[] }>({
    queryKey: ["backtest", "runs", 50],
    queryFn: () => getBacktestRuns(50),
  });

  const detailQ = useQuery<BacktestRunDetail>({
    queryKey: ["backtest", "detail", selectedRunId],
    queryFn: () => getBacktestRunDetail(selectedRunId!),
    enabled: Boolean(selectedRunId),
  });

  const runs = runsQ.data?.runs ?? [];

  return (
    <>
      <Card title="Backtest Runs" extra={<span>last 50 — click a row for details</span>}>
        {runsQ.isLoading && (
          <div className="text-center py-4 text-xs opacity-50">Loading…</div>
        )}
        {runs.length === 0 && !runsQ.isLoading && (
          <div className="alert alert-info bg-base-300 border-base-300">
            <p className="text-base-content/60 italic py-2">
              No backtest runs yet. Run{" "}
              <code className="text-primary">platform backtest</code> from the CLI.
            </p>
          </div>
        )}
        {runs.length > 0 && (
          <div className="overflow-x-auto">
            <table className="table table-zebra table-sm">
              <thead>
                <tr>
                  <th>Started</th>
                  <th>Duration</th>
                  <th>Mode</th>
                  <th>Symbols</th>
                  <th className="text-right">Heartbeats</th>
                  <th className="text-right">Signals</th>
                  <th className="text-right">Approved</th>
                  <th className="text-right">Orders</th>
                  <th className="text-right">Initial</th>
                  <th className="text-right">Final</th>
                  <th className="text-right">Return</th>
                  <th className="text-right">Net PnL</th>
                  <th className="text-right">Max DD</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => {
                  const isActive = selectedRunId === r.run_id;
                  return (
                    <tr
                      key={r.run_id}
                      className={`cursor-pointer hover:bg-base-300 ${isActive ? "bg-primary/10" : ""}`}
                      onClick={() => setSelectedRunId(isActive ? null : r.run_id)}
                    >
                      <td className="font-mono text-xs opacity-60">{fmtTs(r.ts_started)}</td>
                      <td>{(r as Record<string, unknown>).duration_sec as number ?? "—"}s</td>
                      <td>{(r as Record<string, unknown>).mode as string ?? "—"}</td>
                      <td className="font-mono text-xs">{r.symbols?.join(", ") ?? "—"}</td>
                      <td className="text-right">{(r as Record<string, unknown>).n_heartbeats as number ?? 0}</td>
                      <td className="text-right">{(r as Record<string, unknown>).n_signals_produced as number ?? 0}</td>
                      <td className="text-right">{(r as Record<string, unknown>).n_signals_approved as number ?? 0}</td>
                      <td className="text-right">{(r as Record<string, unknown>).n_orders as number ?? 0}</td>
                      <td className="text-right">{fmtUSD((r as Record<string, unknown>).initial_equity as number, { decimals: 0 })}</td>
                      <td className="text-right">{(r as Record<string, unknown>).final_equity ? fmtUSD((r as Record<string, unknown>).final_equity as number, { decimals: 0 }) : "—"}</td>
                      <td className={`text-right ${(((r as Record<string, unknown>).total_return_pct as number) ?? 0) >= 0 ? "text-success" : "text-error"}`}>
                        {fmtNum((r as Record<string, unknown>).total_return_pct as number, 2)}%
                      </td>
                      <td className={`text-right font-mono ${(((r as Record<string, unknown>).total_net_pnl as number) ?? 0) >= 0 ? "text-success" : "text-error"}`}>
                        {fmtUSD((r as Record<string, unknown>).total_net_pnl as number)}
                      </td>
                      <td className="text-right text-error">
                        {fmtPct((r as Record<string, unknown>).max_drawdown_pct as number / 100)}
                      </td>
                      <td>
                        {(r as Record<string, unknown>).error ? (
                          <span className="badge badge-error badge-xs">error</span>
                        ) : (
                          <span className="badge badge-success badge-xs">ok</span>
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

      {/* Drill-down detail panel */}
      {selectedRunId && (
        <BacktestRunDetailPanel
          runId={selectedRunId}
          detail={detailQ.data}
          isLoading={detailQ.isLoading}
          isError={detailQ.isError}
        />
      )}
    </>
  );
}

interface DetailPanelProps {
  runId: string;
  detail?: BacktestRunDetail;
  isLoading: boolean;
  isError: boolean;
}

function BacktestRunDetailPanel({ runId, detail, isLoading, isError }: DetailPanelProps) {
  if (isLoading) {
    return (
      <Card title={`Run ${runId.slice(0, 8)}... — Loading`}>
        <div className="text-center py-8 text-xs opacity-50">Loading tear sheet…</div>
      </Card>
    );
  }
  if (isError || !detail) {
    return (
      <Card title={`Run ${runId.slice(0, 8)}... — Error`}>
        <div className="alert alert-error">Failed to load run detail.</div>
      </Card>
    );
  }

  // Tear sheet structure varies — extract whatever's present.
  const ts = (detail.tear_sheet ?? {}) as Record<string, unknown>;
  const equityCurve = (ts.equity_curve as [string, number][]) ?? [];
  const trades = (ts.trades as Record<string, unknown>[]) ?? [];

  // Convert equity_curve tuple array to PnLHistoryEntry-shaped array for EquityCurve component
  const equityData = equityCurve.map(([t, v]) => ({
    ts: typeof t === "string" ? t : new Date(t as number).toISOString(),
    equity: v,
    drawdown_pct: 0,
  }));

  return (
    <>
      <Card
        title={`Run ${runId.slice(0, 8)}... — Detail`}
        extra={
          <span>
            {detail.mode} · {detail.symbols?.join(", ")} ·{" "}
            {fmtTs(detail.ts_started)} → {fmtTs(detail.ts_finished)}
          </span>
        }
      >
        <StatsGrid
          columns={6}
          stats={[
            { label: "Initial Equity", value: detail.initial_equity, format: "usd", color: "neutral" },
            { label: "Final Equity", value: detail.final_equity, format: "usd", color: detail.final_equity && detail.final_equity >= detail.initial_equity ? "success" : "error" },
            { label: "Total Return", value: (detail.total_return_pct ?? 0) / 100, format: "pct", color: (detail.total_return_pct ?? 0) >= 0 ? "success" : "error" },
            { label: "Net PnL", value: detail.total_net_pnl, format: "usd", color: (detail.total_net_pnl ?? 0) >= 0 ? "success" : "error" },
            { label: "Max DD", value: (detail.max_drawdown_pct ?? 0) / 100, format: "pct", color: "error" },
            { label: "Duration", value: `${detail.duration_sec ?? 0}s`, color: "neutral" },
          ]}
        />
        <div className="divider my-2" />
        <StatsGrid
          columns={6}
          stats={[
            { label: "Heartbeats", value: detail.n_heartbeats ?? 0, color: "primary" },
            { label: "Signals", value: detail.n_signals_produced ?? 0, color: "info" },
            { label: "Approved", value: detail.n_signals_approved ?? 0, color: "success" },
            { label: "Rejected", value: detail.n_signals_rejected ?? 0, color: "error" },
            { label: "Orders", value: detail.n_orders ?? 0, color: "neutral" },
            { label: "Fills", value: detail.n_fills ?? 0, color: "neutral" },
          ]}
        />
        {detail.error && (
          <div className="alert alert-error mt-4">
            <span className="font-mono text-xs">{detail.error}</span>
          </div>
        )}
      </Card>

      {equityData.length > 0 && (
        <Card title="Equity Curve" extra={<span>{equityData.length} data points</span>}>
          <EquityCurve data={equityData} height={360} />
        </Card>
      )}

      {trades.length > 0 && (
        <Card title="Trades" extra={<span>{trades.length} trade(s)</span>}>
          <div className="overflow-x-auto">
            <table className="table table-zebra table-xs">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Symbol</th>
                  <th>Regime</th>
                  <th className="text-right">Net PnL</th>
                  <th className="text-right">R Multiple</th>
                  <th className="text-right">Hold (sec)</th>
                </tr>
              </thead>
              <tbody>
                {trades.slice(0, 100).map((t, i) => {
                  const pnl = t.net_pnl as number | undefined;
                  const r = t.r_multiple as number | undefined;
                  return (
                    <tr key={i}>
                      <td>{i + 1}</td>
                      <td className="font-mono font-semibold">{String(t.symbol ?? "—")}</td>
                      <td>{String(t.regime_at_close ?? "—")}</td>
                      <td className={`text-right font-mono ${pnl !== undefined && pnl >= 0 ? "text-success" : "text-error"}`}>
                        {pnl !== undefined ? fmtUSD(pnl) : "—"}
                      </td>
                      <td className={`text-right font-mono ${r !== undefined && r >= 0 ? "text-success" : "text-error"}`}>
                        {r !== undefined ? fmtNum(r, 2) : "—"}
                      </td>
                      <td className="text-right">{String(t.hold_duration_sec ?? "—")}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {trades.length > 100 && (
              <div className="text-xs opacity-50 text-right mt-2">
                Showing first 100 of {trades.length} trades
              </div>
            )}
          </div>
        </Card>
      )}
    </>
  );
}
