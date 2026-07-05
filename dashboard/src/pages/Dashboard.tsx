import { useQuery } from "@tanstack/react-query";
import { Card } from "@/components/layout/Card";
import { EquityCurve } from "@/components/charts/EquityCurve";
import { StatsGrid } from "@/components/charts/StatsGrid";
import { PositionTable } from "@/components/charts/PositionTable";
import {
  getPortfolioMetrics,
  getTearSheet,
  getPnLHistory,
  getRiskDecisions,
} from "@/lib/api";
import { fmtUSD, fmtTs } from "@/lib/format";
import type { PortfolioMetrics, TearSheet, PnLHistoryEntry, RiskDecision } from "@/lib/types";

export function DashboardPage() {
  // Parallel fetching — TanStack Query handles deduplication and caching.
  const portfolioQ = useQuery<PortfolioMetrics>({
    queryKey: ["portfolio"],
    queryFn: getPortfolioMetrics,
  });
  const tearSheetQ = useQuery<TearSheet>({
    queryKey: ["pnl", "tear_sheet"],
    queryFn: getTearSheet,
  });
  const historyQ = useQuery<PnLHistoryEntry[]>({
    queryKey: ["pnl", "history", 500],
    queryFn: () => getPnLHistory(500),
    // Equity curve doesn't need to refetch every 30s — use a longer interval.
    refetchInterval: 60_000,
  });
  const decisionsQ = useQuery<{ decisions: RiskDecision[] }>({
    queryKey: ["risk", "decisions", 5],
    queryFn: () => getRiskDecisions(5),
  });

  const portfolio = portfolioQ.data;
  const tearSheet = tearSheetQ.data;
  const history = historyQ.data ?? [];
  const decisions = decisionsQ.data?.decisions ?? [];

  const isLoading =
    portfolioQ.isLoading || tearSheetQ.isLoading || historyQ.isLoading;
  const isError =
    portfolioQ.isError || tearSheetQ.isError || historyQ.isError;

  if (isError) {
    return (
      <div className="alert alert-error">
        <span>Failed to load dashboard data. Check the FastAPI backend is running on :8080.</span>
      </div>
    );
  }

  return (
    <>
      {/* === Account overview card === */}
      <Card
        title="Account"
        extra={
          <>
            <span>Equity: <code className="text-primary">{fmtUSD(portfolio?.equity)}</code></span>
            <span>Environment: <code className="text-primary">dev</code></span>
            <span>Updated: {fmtTs(new Date().toISOString())}</span>
          </>
        }
      >
        <StatsGrid
          columns={6}
          stats={[
            { label: "Equity", value: portfolio?.equity, format: "usd", color: "primary", size: "lg" },
            { label: "Cash", value: portfolio?.cash, format: "usd", color: "neutral" },
            { label: "Daily P&L", value: portfolio?.daily_pnl, format: "usd", color: portfolio?.daily_pnl && portfolio.daily_pnl >= 0 ? "success" : "error" },
            { label: "Daily P&L %", value: portfolio?.daily_pnl_pct, format: "pct", color: portfolio?.daily_pnl_pct && portfolio.daily_pnl_pct >= 0 ? "success" : "error" },
            { label: "Leverage", value: portfolio?.leverage, format: "num", decimals: 2, color: "warning" },
            { label: "Gross Exposure", value: portfolio?.gross_exposure_pct, format: "pct", color: "info" },
          ]}
        />
      </Card>

      {/* === Equity curve === */}
      <Card
        title="Equity Curve"
        extra={<span>{history.length} data points · auto-refresh 60s</span>}
      >
        <EquityCurve data={history} />
      </Card>

      {/* === PnL stats grid === */}
      <Card title="Performance Stats">
        <StatsGrid
          columns={6}
          stats={[
            { label: "Total Return", value: tearSheet?.total_return_pct, format: "pct", color: tearSheet?.total_return_pct && tearSheet.total_return_pct >= 0 ? "success" : "error" },
            { label: "Sharpe", value: tearSheet?.sharpe, format: "num", decimals: 2, color: "primary" },
            { label: "Sortino", value: tearSheet?.sortino, format: "num", decimals: 2, color: "primary" },
            { label: "Calmar", value: tearSheet?.calmar, format: "num", decimals: 2, color: "primary" },
            { label: "Max DD", value: tearSheet?.max_drawdown_pct, format: "pct", color: "error" },
            { label: "Win Rate", value: tearSheet?.win_rate, format: "pct", color: "info" },
          ]}
        />
        <div className="divider my-2" />
        <StatsGrid
          columns={6}
          stats={[
            { label: "Profit Factor", value: tearSheet?.profit_factor, format: "num", decimals: 2, color: "neutral" },
            { label: "Avg Win", value: tearSheet?.avg_win, format: "usd", color: "success" },
            { label: "Avg Loss", value: tearSheet?.avg_loss, format: "usd", color: "error" },
            { label: "Avg R", value: tearSheet?.avg_r_multiple, format: "num", decimals: 2, color: "neutral" },
            { label: "Total Trades", value: tearSheet?.total_trades, format: "raw", color: "neutral" },
            { label: "Ulcer Index", value: tearSheet?.ulcer_index, format: "num", decimals: 2, color: "warning" },
          ]}
        />
      </Card>

      {/* === Open positions === */}
      <Card title="Open Positions" extra={<span>{portfolio?.positions?.length || 0} position(s)</span>}>
        <PositionTable positions={portfolio?.positions ?? []} limit={10} />
      </Card>

      {/* === Risk + VaR === */}
      <Card title="Risk & VaR">
        <StatsGrid
          columns={4}
          stats={[
            { label: "VaR 95% (1d)", value: portfolio?.var_95, format: "usd", color: "warning" },
            { label: "VaR 99% (1d)", value: portfolio?.var_99, format: "usd", color: "error" },
            { label: "Max DD %", value: portfolio?.max_drawdown_pct, format: "pct", color: "error" },
            { label: "Total P&L", value: portfolio?.total_pnl, format: "usd", color: portfolio?.total_pnl && portfolio.total_pnl >= 0 ? "success" : "error" },
          ]}
        />
      </Card>

      {/* === Recent risk decisions === */}
      <Card title="Recent Risk Decisions" extra={<span>last 5</span>}>
        {decisions.length === 0 ? (
          <div className="alert alert-info bg-base-300 border-base-300">
            <p className="text-base-content/60 italic py-2">No risk decisions yet</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="table table-sm">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Symbol</th>
                  <th>Action</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {decisions.map((d) => (
                  <tr key={d.decision_id}>
                    <td className="font-mono text-xs opacity-60">{fmtTs(d.ts)}</td>
                    <td className="font-mono">{d.symbol || "—"}</td>
                    <td>
                      <span className="badge badge-ghost badge-sm">{d.action}</span>
                    </td>
                    <td className="text-xs opacity-70">{d.reason || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {isLoading && (
        <div className="text-center py-4 text-xs opacity-50">
          Loading dashboard data…
        </div>
      )}
    </>
  );
}
