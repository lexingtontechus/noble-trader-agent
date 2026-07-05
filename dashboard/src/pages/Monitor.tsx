import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card } from "@/components/layout/Card";
import { StatsGrid } from "@/components/charts/StatsGrid";
import { useTickStream } from "@/lib/ws";
import { listSymbols, getMonitorEvents } from "@/lib/api";
import { fmtTs } from "@/lib/format";
import type { SymbolRow, MonitorEvent } from "@/lib/types";

export function MonitorPage() {
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);

  const symbolsQ = useQuery<{ symbols: SymbolRow[] }>({
    queryKey: ["symbols", { activeOnly: true }],
    queryFn: () => listSymbols({ active_only: true }),
  });
  const eventsQ = useQuery<{ events: MonitorEvent[] }>({
    queryKey: ["monitor", "events", 50],
    queryFn: () => getMonitorEvents(50),
    refetchInterval: 10_000,
  });

  const activeSymbols = symbolsQ.data?.symbols ?? [];
  const tick = useTickStream(selectedSymbol);

  return (
    <>
      <Card title="Live Price Monitor">
        <div className="flex flex-wrap gap-2 mb-4">
          {activeSymbols.map((s) => (
            <button
              key={s.symbol}
              className={`btn btn-sm ${
                selectedSymbol === s.symbol ? "btn-primary" : "btn-ghost"
              }`}
              onClick={() => setSelectedSymbol(s.symbol)}
            >
              {s.symbol}
            </button>
          ))}
          {activeSymbols.length === 0 && (
            <span className="text-xs opacity-50">
              No active symbols. Add some via the Symbols page.
            </span>
          )}
        </div>

        {selectedSymbol && (
          <StatsGrid
            columns={4}
            stats={[
              {
                label: "Symbol",
                value: tick?.symbol ?? selectedSymbol,
                color: "primary",
                size: "lg",
              },
              {
                label: "Last Price",
                value: tick?.price ?? null,
                format: "usd",
                decimals: 4,
                color: "info",
                size: "lg",
              },
              {
                label: "Last Size",
                value: tick?.size ?? null,
                format: "num",
                decimals: 4,
                color: "neutral",
              },
              {
                label: "Last Update",
                value: tick ? fmtTs(tick.ts) : "waiting…",
                color: "neutral",
              },
            ]}
          />
        )}

        {selectedSymbol && !tick && (
          <div className="alert alert-info bg-base-300 border-base-300 mt-3">
            <p className="text-base-content/60 italic py-2">
              Connecting to WebSocket /ws/{selectedSymbol}…
            </p>
          </div>
        )}

        {!selectedSymbol && (
          <div className="alert alert-info bg-base-300 border-base-300">
            <p className="text-base-content/60 italic py-2">
              Select a symbol above to start streaming live ticks.
            </p>
          </div>
        )}
      </Card>

      <Card title="Recent Monitor Events" extra={<span>last 50 · auto-refresh 10s</span>}>
        {eventsQ.isLoading && <div className="text-center py-4 text-xs opacity-50">Loading…</div>}
        {eventsQ.data && eventsQ.data.events.length === 0 && (
          <div className="alert alert-info bg-base-300 border-base-300">
            <p className="text-base-content/60 italic py-2">No monitor events yet</p>
          </div>
        )}
        {eventsQ.data && eventsQ.data.events.length > 0 && (
          <div className="overflow-x-auto">
            <table className="table table-zebra table-sm">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Symbol</th>
                  <th>Event</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {eventsQ.data.events.map((e) => (
                  <tr key={e.event_id}>
                    <td className="font-mono text-xs opacity-60">{fmtTs(e.ts)}</td>
                    <td className="font-mono font-semibold">{e.symbol}</td>
                    <td>
                      <span className="badge badge-ghost badge-sm">{e.event_type}</span>
                    </td>
                    <td className="text-xs opacity-70 font-mono">
                      {e.details ? JSON.stringify(e.details).slice(0, 80) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
