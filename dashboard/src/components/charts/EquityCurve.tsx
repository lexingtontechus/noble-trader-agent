import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { PnLHistoryEntry } from "@/lib/types";
import { fmtUSD, fmtTs } from "@/lib/format";

interface EquityCurveProps {
  data: PnLHistoryEntry[];
  height?: number;
}

export function EquityCurve({ data, height = 320 }: EquityCurveProps) {
  if (!data || data.length === 0) {
    return (
      <div
        className="flex items-center justify-center text-base-content/40 italic"
        style={{ height }}
      >
        No equity history yet
      </div>
    );
  }

  // Format data for Recharts — parse timestamps and use equity as the Y value.
  const chartData = data.map((row) => ({
    ts: row.ts,
    label: fmtTs(row.ts),
    equity: row.equity,
    drawdown_pct: row.drawdown_pct,
  }));

  // Compute domain with small padding above/below for nice rendering
  const equities = chartData.map((d) => d.equity).filter((v) => v != null) as number[];
  const min = Math.min(...equities);
  const max = Math.max(...equities);
  const pad = (max - min) * 0.1 || max * 0.02;
  const yDomain: [number | string, number | string] = [min - pad, max + pad];

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="oklch(var(--p))" stopOpacity={0.4} />
            <stop offset="100%" stopColor="oklch(var(--p))" stopOpacity={0.05} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="oklch(var(--bc) / 0.1)" strokeDasharray="3 3" />
        <XAxis
          dataKey="label"
          tick={{ fill: "oklch(var(--bc) / 0.6)", fontSize: 11 }}
          tickLine={false}
          axisLine={{ stroke: "oklch(var(--bc) / 0.2)" }}
          minTickGap={50}
        />
        <YAxis
          domain={yDomain}
          tick={{ fill: "oklch(var(--bc) / 0.6)", fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v: number) => fmtUSD(v, { decimals: 0 })}
          width={70}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: "oklch(var(--b2))",
            border: "1px solid oklch(var(--bc) / 0.2)",
            borderRadius: "8px",
            fontSize: "12px",
          }}
          labelStyle={{ color: "oklch(var(--bc))" }}
          formatter={(value: number) => [fmtUSD(value), "Equity"]}
        />
        <Area
          type="monotone"
          dataKey="equity"
          stroke="oklch(var(--p))"
          strokeWidth={2}
          fill="url(#equityGradient)"
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
