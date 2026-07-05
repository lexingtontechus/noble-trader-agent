import { useQuery } from "@tanstack/react-query";
import { Card } from "@/components/layout/Card";
import { StatsGrid } from "@/components/charts/StatsGrid";
import { AllocationPie } from "@/components/charts/AllocationPie";
import { ExposureBars } from "@/components/charts/ExposureBars";
import { VarDistHistogram } from "@/components/charts/VarDistHistogram";
import {
  getPortfolioMetrics,
  getPortfolioExposure,
  getPortfolioVarHistory,
  getRiskDecisions,
} from "@/lib/api";
import { fmtPct, fmtUSD, fmtTs } from "@/lib/format";
import type {
  PortfolioMetrics,
  ExposureBreakdown,
  VarHistoryEntry,
  RiskDecision,
} from "@/lib/types";

// Pragmatic helper: PortfolioMetrics has a [key: string]: unknown index signature
// for forward-compat with backend additions. We cast through any for property access.
const m = (metrics: PortfolioMetrics | undefined): any => metrics;

export function PortfolioPage() {
  const metricsQ = useQuery<PortfolioMetrics>({
    queryKey: ["portfolio"],
    queryFn: getPortfolioMetrics,
  });
  const exposureQ = useQuery<ExposureBreakdown>({
    queryKey: ["portfolio", "exposure"],
    queryFn: getPortfolioExposure,
  });
  const varHistoryQ = useQuery<{ history: VarHistoryEntry[] }>({
    queryKey: ["portfolio", "var_history", 500],
    queryFn: () => getPortfolioVarHistory(500),
    refetchInterval: 60_000,
  });
  const decisionsQ = useQuery<{ decisions: RiskDecision[] }>({
    queryKey: ["risk", "decisions", 50],
    queryFn: () => getRiskDecisions(50),
  });

  const metrics = m(metricsQ.data);
  const exposure = exposureQ.data;
  const varHistory = varHistoryQ.data?.history ?? [];
  const decisions = decisionsQ.data?.decisions ?? [];

  // Build pie data from by_venue or by_direction
  const pieData = exposure?.by_venue
    ? Object.entries(exposure.by_venue)
        .filter(([_, v]) => Math.abs(v) > 0.01)
        .map(([name, value]) => ({ name, value: Math.abs(value) }))
    : [];

  const directionBars = exposure?.by_direction
    ? Object.entries(exposure.by_direction).map(([k, v]) => ({
        label: k,
        value: v,
        color: k === "long" ? "oklch(var(--su))" : k === "short" ? "oklch(var(--er))" : "oklch(var(--n))",
      }))
    : [];

  const totals = exposure?.totals;
  const totalsBars = totals
    ? [
        { label: "Long", value: totals.long_exposure_usd, color: "oklch(var(--su))" },
        { label: "Short", value: totals.short_exposure_usd, color: "oklch(var(--er))" },
        { label: "Gross", value: totals.gross_exposure_usd, color: "oklch(var(--p))" },
        { label: "Net", value: Math.abs(totals.net_exposure_usd), color: "oklch(var(--a))" },
      ]
    : [];

  // VaR values for histogram (filter nulls)
  const varValues = varHistory
    .map((h) => h.var_1d_99)
    .filter((v): v is number => v !== null && v !== undefined);

  // Latest snapshot for current values
  const latest = varHistory[varHistory.length - 1];

  return (
    <>
      {/* Account overview */}
      <Card
        title="Account"
        extra={<span>Updated: {fmtTs(new Date().toISOString())}</span>}
      >
        <StatsGrid
          columns={6}
          stats={[
            { label: "Equity Total", value: metrics?.equity_total ?? metrics?.equity, format: "usd", color: "primary", size: "lg" },
            { label: "Cash USD", value: metrics?.cash_usd ?? metrics?.cash, format: "usd", color: "neutral" },
            { label: "Cash USDC", value: metrics?.cash_usdc, format: "usd", color: "neutral" },
            { label: "Gross Leverage", value: metrics?.leverage_gross ?? metrics?.leverage, format: "num", decimals: 4, color: "warning" },
            { label: "Net Leverage", value: metrics?.leverage_net, format: "num", decimals: 4, color: "info" },
            { label: "Open Positions", value: metrics?.n_open_positions ?? metrics?.positions?.length ?? 0, color: "neutral" },
          ]}
        />
      </Card>

      {/* Exposure + allocation */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card title="Allocation by Venue" extra={<span>PnL contribution (30d)</span>}>
          {pieData.length > 0 ? (
            <AllocationPie data={pieData} />
          ) : totals ? (
            <AllocationPie
              data={[
                { name: "Long", value: totals.long_exposure_usd, color: "oklch(var(--su))" },
                { name: "Short", value: totals.short_exposure_usd, color: "oklch(var(--er))" },
              ]}
            />
          ) : (
            <div className="flex items-center justify-center h-72 text-base-content/40 italic">
              No allocation data yet — run <code className="text-primary mx-1">platform risk</code> to start
            </div>
          )}
        </Card>

        <Card title="Exposure Breakdown">
          {directionBars.length > 0 ? (
            <ExposureBars data={directionBars} />
          ) : totalsBars.length > 0 ? (
            <ExposureBars data={totalsBars} />
          ) : (
            <div className="flex items-center justify-center h-56 text-base-content/40 italic">
              No exposure data yet
            </div>
          )}
          {exposure?.n_trades_30d !== undefined && (
            <div className="text-xs opacity-60 mt-2 text-center">
              {exposure.n_trades_30d} closed trades in last 30 days
            </div>
          )}
        </Card>
      </div>

      {/* PnL breakdown */}
      <Card title="PnL Breakdown">
        <StatsGrid
          columns={6}
          stats={[
            { label: "Realized PnL", value: metrics?.realized_pnl, format: "usd", color: (metrics?.realized_pnl ?? 0) >= 0 ? "success" : "error" },
            { label: "Unrealized PnL", value: metrics?.unrealized_pnl, format: "usd", color: (metrics?.unrealized_pnl ?? 0) >= 0 ? "success" : "error" },
            { label: "Funding PnL", value: metrics?.funding_pnl, format: "usd", color: "neutral" },
            { label: "Fees Paid", value: metrics?.fees_paid, format: "usd", color: "error" },
            { label: "Total PnL", value: (metrics?.realized_pnl ?? 0) + (metrics?.unrealized_pnl ?? 0), format: "usd", color: ((metrics?.realized_pnl ?? 0) + (metrics?.unrealized_pnl ?? 0)) >= 0 ? "success" : "error" },
            { label: "Peak Equity", value: metrics?.peak_equity, format: "usd", color: "primary" },
          ]}
        />
        <div className="divider my-2" />
        <StatsGrid
          columns={6}
          stats={[
            { label: "Drawdown %", value: metrics?.drawdown_pct, format: "pct", color: "error" },
            { label: "Drawdown $", value: metrics?.drawdown_usd, format: "usd", color: "error" },
            { label: "Time in DD", value: `${metrics?.time_in_dd_sec ?? 0}s`, color: "warning" },
            { label: "VaR 1d 99%", value: metrics?.var_1d_99, format: "usd", color: "warning" },
            { label: "CVaR 1d 99%", value: metrics?.cvar_1d_99, format: "usd", color: "error" },
            { label: "Venues", value: metrics?.n_venues, color: "neutral" },
          ]}
        />
      </Card>

      {/* VaR distribution histogram */}
      <Card title="VaR 1d 99% Distribution" extra={<span>{varValues.length} snapshots</span>}>
        <VarDistHistogram values={varValues} />
        {latest && (
          <div className="text-xs opacity-60 mt-2 grid grid-cols-3 gap-2 text-center">
            <div>
              Latest VaR: <span className="font-mono text-warning">{fmtUSD(latest.var_1d_99)}</span>
            </div>
            <div>
              Latest CVaR: <span className="font-mono text-error">{fmtUSD(latest.cvar_1d_99)}</span>
            </div>
            <div>
              Latest DD: <span className="font-mono text-error">{fmtPct(latest.drawdown_pct)}</span>
            </div>
          </div>
        )}
      </Card>

      {/* Risk decisions table */}
      <Card title="Risk Decisions (last 50)" extra={<span>from portfolio risk engine</span>}>
        {decisions.length === 0 ? (
          <div className="alert alert-info bg-base-300 border-base-300">
            <p className="text-base-content/60 italic py-2">
              No risk decisions yet. Run{" "}
              <code className="text-primary">platform risk --equity 100000</code> and{" "}
              <code className="text-primary">platform synthesize</code>.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="table table-zebra table-sm">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Symbol</th>
                  <th>Action</th>
                  <th className="text-right">Approved</th>
                  <th className="text-right">Requested</th>
                  <th className="text-right">Approved $</th>
                  <th>CB Level</th>
                  <th>Tier</th>
                  <th className="text-right">VaR Pre</th>
                  <th className="text-right">VaR Post</th>
                  <th>Limits Hit</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {decisions.map((d) => {
                  const detail = (d.details ?? {}) as Record<string, unknown>;
                  const approved = detail.approved as boolean | undefined;
                  const requested = detail.requested_size_usd as number | undefined;
                  const approvedSize = detail.approved_size_usd as number | undefined;
                  const cbLevel = detail.circuit_breaker_level as string | undefined;
                  const tier = detail.autonomy_tier as string | undefined;
                  const varPre = detail.var_pre as number | undefined;
                  const varPost = detail.var_post as number | undefined;
                  const limits = detail.limits_hit as string | undefined;
                  return (
                    <tr key={d.decision_id}>
                      <td className="font-mono text-xs opacity-60">{fmtTs(d.ts)}</td>
                      <td className="font-mono font-semibold">{d.symbol ?? "—"}</td>
                      <td>
                        <span className="badge badge-ghost badge-xs">{d.action}</span>
                      </td>
                      <td>
                        {approved === undefined ? (
                          "—"
                        ) : approved ? (
                          <span className="badge badge-success badge-xs">approved</span>
                        ) : (
                          <span className="badge badge-error badge-xs">rejected</span>
                        )}
                      </td>
                      <td className="text-right font-mono">{requested ? fmtUSD(requested, { decimals: 0 }) : "—"}</td>
                      <td className={`text-right font-mono ${approvedSize && approvedSize > 0 ? "text-success" : "text-error"}`}>
                        {approvedSize !== undefined ? fmtUSD(approvedSize, { decimals: 0 }) : "—"}
                      </td>
                      <td>{cbLevel ?? "—"}</td>
                      <td className="text-xs">{tier ?? "—"}</td>
                      <td className="text-right font-mono text-xs">{varPre !== undefined ? fmtUSD(varPre, { decimals: 0 }) : "—"}</td>
                      <td className="text-right font-mono text-xs">{varPost !== undefined ? fmtUSD(varPost, { decimals: 0 }) : "—"}</td>
                      <td className="text-xs opacity-70">{limits ? String(limits).slice(0, 40) : "—"}</td>
                      <td className="text-xs opacity-70">{d.reason ? d.reason.slice(0, 40) : "—"}</td>
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
