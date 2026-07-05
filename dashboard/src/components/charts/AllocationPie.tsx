import {
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import { fmtUSD } from "@/lib/format";

interface PieDatum {
  name: string;
  value: number;
  color?: string;
}

interface AllocationPieProps {
  data: PieDatum[];
  height?: number;
  format?: "usd" | "num";
}

/** Pie / donut chart for allocation breakdowns. */
export function AllocationPie({
  data,
  height = 280,
  format = "usd",
}: AllocationPieProps) {
  if (!data || data.length === 0) {
    return (
      <div
        className="flex items-center justify-center text-base-content/40 italic"
        style={{ height }}
      >
        No allocation data yet
      </div>
    );
  }

  const formatter = (v: number) =>
    format === "usd" ? fmtUSD(v, { decimals: 0 }) : v.toLocaleString();

  // DaisyUI semantic colors
  const palette = [
    "oklch(var(--p))",
    "oklch(var(--s))",
    "oklch(var(--a))",
    "oklch(var(--su))",
    "oklch(var(--wa))",
    "oklch(var(--er))",
    "oklch(var(--in))",
    "oklch(var(--n))",
  ];

  return (
    <ResponsiveContainer width="100%" height={height}>
      <PieChart>
        <Pie
          data={data}
          dataKey="value"
          nameKey="name"
          cx="50%"
          cy="50%"
          innerRadius={60}
          outerRadius={100}
          paddingAngle={2}
          isAnimationActive={false}
        >
          {data.map((d, i) => (
            <Cell key={i} fill={d.color || palette[i % palette.length]} />
          ))}
        </Pie>
        <Tooltip
          contentStyle={{
            backgroundColor: "oklch(var(--b2))",
            border: "1px solid oklch(var(--bc) / 0.2)",
            borderRadius: "8px",
            fontSize: "12px",
          }}
          formatter={(value: number, name: string) => [formatter(value), name]}
        />
        <Legend
          wrapperStyle={{ fontSize: 12 }}
          iconType="circle"
        />
      </PieChart>
    </ResponsiveContainer>
  );
}
