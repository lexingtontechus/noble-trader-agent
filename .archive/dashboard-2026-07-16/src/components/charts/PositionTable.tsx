import type { Position } from "@/lib/types";
import { fmtNum, fmtUSD, pnlColor } from "@/lib/format";

interface PositionTableProps {
  positions: Position[];
  limit?: number;
}

export function PositionTable({ positions, limit = 10 }: PositionTableProps) {
  if (!positions || positions.length === 0) {
    return (
      <div className="alert alert-info bg-base-300 border-base-300">
        <div className="flex flex-col items-center w-full py-4">
          <p className="text-base-content/60 italic">No open positions</p>
        </div>
      </div>
    );
  }

  const rows = positions.slice(0, limit);

  return (
    <div className="overflow-x-auto">
      <table className="table table-zebra table-sm">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Side</th>
            <th className="text-right">Qty</th>
            <th className="text-right">Entry</th>
            <th className="text-right">Current</th>
            <th className="text-right">Stop</th>
            <th className="text-right">Target</th>
            <th className="text-right">uPnL</th>
            <th>Opened</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p, i) => {
            const pnl = p.unrealized_pnl ?? 0;
            return (
              <tr key={p.position_id || `${p.symbol}-${i}`}>
                <td className="font-semibold font-mono">{p.symbol}</td>
                <td>
                  {p.direction === "long" ? (
                    <span className="badge badge-success badge-xs">long</span>
                  ) : p.direction === "short" ? (
                    <span className="badge badge-error badge-xs">short</span>
                  ) : (
                    <span className="badge badge-ghost badge-xs">{p.direction}</span>
                  )}
                </td>
                <td className="text-right font-mono">{fmtNum(p.qty, 4)}</td>
                <td className="text-right font-mono">{fmtUSD(p.entry_price)}</td>
                <td className="text-right font-mono">
                  {p.current_price ? fmtUSD(p.current_price) : "—"}
                </td>
                <td className="text-right font-mono">
                  {p.stop_price ? fmtUSD(p.stop_price) : "—"}
                </td>
                <td className="text-right font-mono">
                  {p.target_price ? fmtUSD(p.target_price) : "—"}
                </td>
                <td className={`text-right font-mono ${pnlColor(pnl)}`}>
                  {p.unrealized_pnl != null ? fmtUSD(pnl) : "—"}
                </td>
                <td className="text-xs opacity-60 font-mono">
                  {p.opened_at ? p.opened_at.slice(0, 19).replace("T", " ") : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {positions.length > limit && (
        <div className="text-xs opacity-50 text-right mt-2">
          Showing {limit} of {positions.length} positions
        </div>
      )}
    </div>
  );
}
