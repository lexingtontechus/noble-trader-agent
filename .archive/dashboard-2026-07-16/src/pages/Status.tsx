import { useQuery } from "@tanstack/react-query";
import { Card } from "@/components/layout/Card";
import { StatsGrid } from "@/components/charts/StatsGrid";
import { getStatus } from "@/lib/api";
import { fmtTs } from "@/lib/format";
import type { StatusResponse } from "@/lib/types";

export function StatusPage() {
  const q = useQuery<StatusResponse>({
    queryKey: ["status"],
    queryFn: getStatus,
    refetchInterval: 10_000, // 10s — matches old Jinja2 meta-refresh
  });

  if (q.isLoading) {
    return <div className="text-center py-4 text-xs opacity-50">Loading status…</div>;
  }
  if (q.isError || !q.data) {
    return (
      <div className="alert alert-error">
        <span>Failed to load status. Is the FastAPI backend running?</span>
      </div>
    );
  }

  const s = q.data;
  const stats = s.ingest_stats;

  return (
    <>
      <Card
        title={`Overall: ${s.overall}`}
        extra={
          <>
            <span>Auto-refreshes every 10s</span>
            <span>Last checked: {fmtTs(s.checked_at)}Z</span>
          </>
        }
      />

      <Card title="Subsystem Connections">
        <div className="divide-y divide-base-300">
          {s.subsystems.map((sub) => (
            <div
              key={sub.name}
              className="flex justify-between items-start py-3 border-b border-base-300 last:border-0"
            >
              <div>
                <div className="font-semibold text-sm">{sub.name}</div>
                {sub.detail && (
                  <div className="text-xs opacity-60 mt-1 break-all">{sub.detail}</div>
                )}
                {sub.url && (
                  <div className="text-xs opacity-40 font-mono mt-1">{sub.url}</div>
                )}
                {sub.channel && (
                  <div className="text-xs opacity-40 font-mono mt-1">
                    channel: {sub.channel}
                  </div>
                )}
                {sub.path && (
                  <div className="text-xs opacity-40 font-mono mt-1">path: {sub.path}</div>
                )}
                {sub.account && (
                  <div className="text-xs opacity-40 font-mono mt-1">
                    account: {sub.account.number} · equity: ${sub.account.equity}
                  </div>
                )}
              </div>
              <div>{statusBadge(sub.status)}</div>
            </div>
          ))}
        </div>
      </Card>

      {stats && stats.total > 0 && (
        <Card title="Heartbeat Ingest Stats">
          <StatsGrid
            columns={6}
            stats={[
              { label: "Total", value: stats.total, color: "primary" },
              { label: "Accepted", value: stats.accepted, color: "success" },
              { label: "Rejected", value: stats.rejected, color: "error" },
              { label: "Last Hour", value: stats.last_hour, color: "info" },
              { label: "Regime Shifts", value: stats.regime_shifts, color: "warning" },
              { label: "Symbols Seen", value: stats.by_symbol?.length ?? 0, color: "info" },
            ]}
          />

          {stats.by_symbol && stats.by_symbol.length > 0 && (
            <>
              <div className="divider"></div>
              <h3 className="text-sm font-semibold mb-2">By Symbol (top 10)</h3>
              <div className="overflow-x-auto">
                <table className="table table-zebra table-sm">
                  <thead>
                    <tr>
                      <th>Symbol</th>
                      <th>Count</th>
                      <th>Last Seen</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stats.by_symbol.slice(0, 10).map((row) => (
                      <tr key={row.symbol}>
                        <td className="font-semibold font-mono">{row.symbol}</td>
                        <td>{row.count}</td>
                        <td className="font-mono text-xs opacity-60">
                          {fmtTs(row.last_seen)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </Card>
      )}
    </>
  );
}

function statusBadge(status: string) {
  const cls = (
    ["connected", "ok", "live", "filled", "approved"].includes(status)
      ? "badge-success"
      : ["error", "rejected", "canceled", "expired", "failed"].includes(status)
      ? "badge-error"
      : ["warning", "partial", "stale", "pending", "shadow", "block"].includes(status)
      ? "badge-warning"
      : ["not_configured", "disabled", "draft", "proposed", "skip"].includes(status)
      ? "badge-ghost"
      : "badge-info"
  );
  return <span className={`badge ${cls} badge-sm`}>{status}</span>;
}
