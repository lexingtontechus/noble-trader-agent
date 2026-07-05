import { useQuery } from "@tanstack/react-query";
import { Card } from "@/components/layout/Card";
import { getBacktestRuns } from "@/lib/api";
import { fmtTs, fmtUSD, fmtPct, fmtNum } from "@/lib/format";
import type { BacktestRun } from "@/lib/types";

export function BacktestPage() {
  const q = useQuery<{ runs: BacktestRun[] }>({
    queryKey: ["backtest", "runs", 20],
    queryFn: () => getBacktestRuns(20),
  });

  const runs = q.data?.runs ?? [];

  return (
    <Card title="Backtest Runs" extra={<span>last 20</span>}>
      {q.isLoading && <div className="text-center py-4 text-xs opacity-50">Loading…</div>}
      {runs.length === 0 && !q.isLoading && (
        <div className="alert alert-info bg-base-300 border-base-300">
          <p className="text-base-content/60 italic py-2">
            No backtest runs yet. Run <code className="text-primary">platform backtest</code> from the CLI.
          </p>
        </div>
      )}
      {runs.length > 0 && (
        <div className="overflow-x-auto">
          <table className="table table-zebra table-sm">
            <thead>
              <tr>
                <th>Started</th>
                <th>Finished</th>
                <th>Symbols</th>
                <th className="text-right">Trades</th>
                <th className="text-right">Sharpe</th>
                <th className="text-right">Win %</th>
                <th className="text-right">Max DD</th>
                <th className="text-right">Net PnL</th>
                <th>Accepted</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.run_id}>
                  <td className="font-mono text-xs opacity-60">{fmtTs(r.ts_started)}</td>
                  <td className="font-mono text-xs opacity-60">{fmtTs(r.ts_finished)}</td>
                  <td className="font-mono text-xs">{r.symbols.join(", ")}</td>
                  <td className="text-right">{r.n_trades}</td>
                  <td className="text-right">{fmtNum(r.sharpe, 2)}</td>
                  <td className="text-right">{fmtPct(r.win_rate)}</td>
                  <td className="text-right text-error">{fmtPct(r.max_drawdown_pct)}</td>
                  <td className={`text-right font-mono ${r.net_pnl_usd >= 0 ? "text-success" : "text-error"}`}>
                    {fmtUSD(r.net_pnl_usd)}
                  </td>
                  <td>
                    {r.accepted ? (
                      <span className="badge badge-success badge-xs">yes</span>
                    ) : (
                      <span className="badge badge-ghost badge-xs">no</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
