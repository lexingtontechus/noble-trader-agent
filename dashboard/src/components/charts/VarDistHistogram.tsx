import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fmtUSD } from "@/lib/format";

interface VarDistHistogramProps {
  values: number[];
  bins?: number;
  height?: number;
}

/** Histogram of VaR values (or any numeric distribution). */
export function VarDistHistogram({
  values,
  bins = 20,
  height = 220,
}: VarDistHistogramProps) {
  if (!values || values.length === 0) {
    return (
      <div
        className="flex items-center justify-center text-base-content/40 italic"
        style={{ height }}
      >
        No VaR history yet
      </div>
    );
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const width = (max - min) / bins || 1;
  const histogram = Array.from({ length: bins }, (_, i) => ({
    range: `${fmtUSD(min + i * width, { decimals: 0 })}`,
    rangeStart: min + i * width,
    count: 0,
  }));
  for (const v of values) {
    const idx = Math.min(Math.floor((v - min) / width), bins - 1);
    if (idx >= 0) histogram[idx].count += 1;
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={histogram} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
        <CartesianGrid stroke="oklch(var(--bc) / 0.1)" strokeDasharray="3 3" />
        <XAxis
          dataKey="range"
          tick={{ fill: "oklch(var(--bc) / 0.6)", fontSize: 10 }}
          tickLine={false}
          axisLine={{ stroke: "oklch(var(--bc) / 0.2)" }}
          minTickGap={20}
        />
        <YAxis
          tick={{ fill: "oklch(var(--bc) / 0.6)", fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          allowDecimals={false}
          width={32}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: "oklch(var(--b2))",
            border: "1px solid oklch(var(--bc) / 0.2)",
            borderRadius: "8px",
            fontSize: "12px",
          }}
          formatter={(value: number) => [value, "Snapshots"]}
          labelFormatter={(label: string) => `VaR ≈ ${label}`}
        />
        <Bar dataKey="count" fill="oklch(var(--wa))" radius={[2, 2, 0, 0]}>
          {histogram.map((bin, i) => (
            <Cell
              key={i}
              fill={
                bin.rangeStart < 0
                  ? "oklch(var(--er))"
                  : "oklch(var(--su))"
              }
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
