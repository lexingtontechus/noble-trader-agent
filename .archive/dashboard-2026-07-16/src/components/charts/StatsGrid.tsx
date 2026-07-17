import { cn, fmtNum, fmtPct, fmtUSD } from "@/lib/format";

interface StatItem {
  label: string;
  value: number | string | null | undefined;
  format?: "usd" | "pct" | "num" | "raw";
  decimals?: number;
  color?: "primary" | "success" | "error" | "warning" | "info" | "neutral";
  size?: "sm" | "md" | "lg";
}

interface StatsGridProps {
  stats: StatItem[];
  columns?: 2 | 3 | 4 | 6;
}

export function StatsGrid({ stats, columns = 4 }: StatsGridProps) {
  const colClass = {
    2: "grid-cols-2",
    3: "grid-cols-2 md:grid-cols-3",
    4: "grid-cols-2 md:grid-cols-4",
    6: "grid-cols-2 md:grid-cols-3 lg:grid-cols-6",
  }[columns];

  return (
    <div className={`grid ${colClass} gap-2`}>
      {stats.map((s, i) => (
        <StatCard key={i} {...s} />
      ))}
    </div>
  );
}

export function StatCard({
  label,
  value,
  format = "raw",
  decimals,
  color = "primary",
  size = "md",
}: StatItem) {
  const formatted = formatValue(value, format, decimals);
  const sizeClass = size === "lg" ? "text-3xl" : size === "sm" ? "text-xl" : "text-2xl";
  const colorClass = `text-${color}`;

  return (
    <div className="stat bg-base-200 rounded-box shadow">
      <div className={cn("stat-value", sizeClass, colorClass)}>{formatted}</div>
      <div className="stat-desc text-xs uppercase tracking-wide opacity-60">
        {label}
      </div>
    </div>
  );
}

function formatValue(
  value: number | string | null | undefined,
  format: StatItem["format"],
  decimals?: number,
): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  switch (format) {
    case "usd":
      return fmtUSD(value, { decimals });
    case "pct":
      return fmtPct(value, decimals);
    case "num":
      return fmtNum(value, decimals);
    case "raw":
    default:
      return String(value);
  }
}
