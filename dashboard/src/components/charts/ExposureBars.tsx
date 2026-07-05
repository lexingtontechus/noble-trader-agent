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

interface BarDatum {
  label: string;
  value: number;
  color?: string;
}

interface ExposureBarsProps {
  data: BarDatum[];
  height?: number;
  format?: "usd" | "num";
}

/** Horizontal bar chart for exposure / allocation breakdowns. */
export function ExposureBars({
  data,
  height = 220,
  format = "usd",
}: ExposureBarsProps) {
  if (!data || data.length === 0) {
    return (
      <div
        className="flex items-center justify-center text-base-content/40 italic"
        style={{ height }}
      >
        No data
      </div>
    );
  }

  const formatter = (v: number) =>
    format === "usd" ? fmtUSD(v, { decimals: 0 }) : v.toLocaleString();

  // Color palette matches DaisyUI semantic colors via oklch() CSS vars
  const palette = ["--p", "--s", "--a", "--su", "--wa", "--er", "--in", "--n"];

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 4, right: 16, bottom: 4, left: 8 }}
      >
        <CartesianGrid stroke="oklch(var(--bc) / 0.1)" strokeDasharray="3 3" />
        <XAxis
          type="number"
          tick={{ fill: "oklch(var(--bc) / 0.6)", fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          tickFormatter={formatter}
        />
        <YAxis
          type="category"
          dataKey="label"
          tick={{ fill: "oklch(var(--bc) / 0.7)", fontSize: 12 }}
          tickLine={false}
          axisLine={false}
          width={100}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: "oklch(var(--b2))",
            border: "1px solid oklch(var(--bc) / 0.2)",
            borderRadius: "8px",
            fontSize: "12px",
          }}
          formatter={(value: number) => [formatter(value), "Value"]}
        />
        <Bar dataKey="value" radius={[0, 4, 4, 0]}>
          {data.map((d, i) => (
            <Cell
              key={i}
              fill={d.color || `oklch(var(${palette[i % palette.length]}))`}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
