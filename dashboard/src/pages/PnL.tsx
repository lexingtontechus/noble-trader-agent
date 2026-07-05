import { useQuery } from "@tanstack/react-query";
import { Card } from "@/components/layout/Card";
import { StatsGrid } from "@/components/charts/StatsGrid";
import { EquityCurve } from "@/components/charts/EquityCurve";
import { getTearSheet, getPnLHistory } from "@/lib/api";
import type { TearSheet, PnLHistoryEntry } from "@/lib/types";

export function PnLPage() {
  const tearSheetQ = useQuery<TearSheet>({
    queryKey: ["pnl", "tear_sheet"],
    queryFn: getTearSheet,
  });
  const historyQ = useQuery<PnLHistoryEntry[]>({
    queryKey: ["pnl", "history", 1000],
    queryFn: () => getPnLHistory(1000),
    refetchInterval: 60_000,
  });

  const ts = tearSheetQ.data;
  const history = historyQ.data ?? [];

  return (
    <>
      <Card title="Equity Curve" extra={<span>{history.length} data points</span>}>
        <EquityCurve data={history} height={400} />
      </Card>

      <Card title="Tear Sheet — Returns">
        <StatsGrid
          columns={6}
          stats={[
            { label: "Total Return", value: ts?.total_return_pct, format: "pct", color: ts?.total_return_pct && ts.total_return_pct >= 0 ? "success" : "error" },
            { label: "Best Trade", value: ts?.best_trade_pct, format: "pct", color: "success" },
            { label: "Worst Trade", value: ts?.worst_trade_pct, format: "pct", color: "error" },
            { label: "Profit Factor", value: ts?.profit_factor, format: "num", decimals: 2, color: "neutral" },
            { label: "Total Trades", value: ts?.total_trades, color: "neutral" },
            { label: "Avg Hold (min)", value: ts?.avg_hold_minutes, format: "num", decimals: 0, color: "neutral" },
          ]}
        />
      </Card>

      <Card title="Tear Sheet — Risk-Adjusted">
        <StatsGrid
          columns={6}
          stats={[
            { label: "Sharpe", value: ts?.sharpe, format: "num", decimals: 3, color: "primary" },
            { label: "Sortino", value: ts?.sortino, format: "num", decimals: 3, color: "primary" },
            { label: "Calmar", value: ts?.calmar, format: "num", decimals: 3, color: "primary" },
            { label: "Ulcer Index", value: ts?.ulcer_index, format: "num", decimals: 3, color: "warning" },
            { label: "Max DD %", value: ts?.max_drawdown_pct, format: "pct", color: "error" },
            { label: "Max DD $", value: ts?.max_drawdown_usd, format: "usd", color: "error" },
          ]}
        />
      </Card>

      <Card title="Tear Sheet — Trade Stats">
        <StatsGrid
          columns={6}
          stats={[
            { label: "Win Rate", value: ts?.win_rate, format: "pct", color: "info" },
            { label: "Avg Win", value: ts?.avg_win, format: "usd", color: "success" },
            { label: "Avg Loss", value: ts?.avg_loss, format: "usd", color: "error" },
            { label: "Avg R", value: ts?.avg_r_multiple, format: "num", decimals: 3, color: "neutral" },
            { label: "Total Trades", value: ts?.total_trades, color: "neutral" },
            { label: "Profit Factor", value: ts?.profit_factor, format: "num", decimals: 2, color: "neutral" },
          ]}
        />
      </Card>

      {ts?.by_regime && Object.keys(ts.by_regime).length > 0 && (
        <Card title="PnL by Regime">
          <div className="overflow-x-auto">
            <table className="table table-zebra table-sm">
              <thead>
                <tr>
                  <th>Regime</th>
                  <th className="text-right">Trades</th>
                  <th className="text-right">PnL %</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(ts.by_regime).map(([regime, stats]) => (
                  <tr key={regime}>
                    <td className="font-mono">{regime}</td>
                    <td className="text-right">{stats.trades}</td>
                    <td className={`text-right ${(stats.pnl_pct || 0) >= 0 ? "text-success" : "text-error"}`}>
                      {((stats.pnl_pct || 0) * 100).toFixed(2)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </>
  );
}
