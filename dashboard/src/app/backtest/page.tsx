"use client";

import { useEffect, useState } from "react";
import { Card } from "@/components/layout/Card";
import { getBacktestRuns, getSimulations } from "@/lib/api-mock";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";

interface BacktestRun {
  id: string;
  strategy: string;
  start_date: string;
  end_date: string;
  total_return: number;
  sharpe_ratio: number;
  max_drawdown: number;
  status: "completed" | "running" | "failed";
}

interface Simulation {
  id: string;
  name: string;
  parameters: Record<string, any>;
  result: {
    total_pnl: number;
    win_rate: number;
    sharpe_ratio: number;
  };
  status: "completed" | "running" | "pending";
}

export default function BacktestPage() {
  const [backtestRuns, setBacktestRuns] = useState<BacktestRun[]>([]);
  const [simulations, setSimulations] = useState<Simulation[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [backtestRes, simRes] = await Promise.all([
          getBacktestRuns(10),
          getSimulations(10)
        ]);

        setBacktestRuns(backtestRes.runs);
        setSimulations(simRes.simulations);
      } catch (error) {
        console.error("Failed to load backtest data:", error);
      } finally {
        setIsLoading(false);
      }
    };

    fetchData();
  }, []);

  const getStatusColor = (status: string) => {
    switch (status) {
      case "completed": return "badge-success";
      case "running": return "badge-warning";
      case "failed": return "badge-error";
      case "pending": return "badge-info";
      default: return "badge";
    }
  };

  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
    }).format(value);
  };

  const formatPercent = (value: number) => {
    return `${(value * 100).toFixed(2)}%`;
  };

  const performanceData = Array.from({ length: 30 }, (_, i) => ({
    day: i + 1,
    portfolio: 100000 + Math.random() * 50000 - 10000,
    benchmark: 100000 + Math.random() * 30000 - 5000,
  }));

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <span className="loading loading-spinner loading-lg text-primary" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Backtest & Simulation</h1>
        <p className="text-base-content opacity-70">Strategy testing and simulation results</p>
      </div>

      {/* Performance Chart */}
      <Card title="Strategy Performance">
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={performanceData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="day" />
              <YAxis tickFormatter={(value) => `$${(value / 1000).toFixed(0)}k`} />
              <Tooltip formatter={(value: number, name: string) => [
                formatCurrency(value),
                name === "portfolio" ? "Portfolio" : "Benchmark"
              ]} />
              <Line type="monotone" dataKey="portfolio" stroke="#8884d8" strokeWidth={2} />
              <Line type="monotone" dataKey="benchmark" stroke="#82ca9d" strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </Card>

      {/* Backtest Results */}
      <Card title="Recent Backtests">
        <div className="overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>Strategy</th>
                <th>Date Range</th>
                <th>Total Return</th>
                <th>Sharpe Ratio</th>
                <th>Max Drawdown</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {backtestRuns.map((run) => (
                <tr key={run.id}>
                  <td className="font-mono">{run.strategy}</td>
                  <td>
                    {new Date(run.start_date).toLocaleDateString()} - {new Date(run.end_date).toLocaleDateString()}
                  </td>
                  <td className={run.total_return >= 0 ? "text-success" : "text-error"}>
                    {formatPercent(run.total_return)}
                  </td>
                  <td>{run.sharpe_ratio.toFixed(2)}</td>
                  <td className="text-error">{formatPercent(run.max_drawdown)}</td>
                  <td>
                    <span className={`badge ${getStatusColor(run.status)}`}>
                      {run.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Simulations */}
      <Card title="Simulations">
        <div className="space-y-4">
          {simulations.map((sim) => (
            <div key={sim.id} className="border rounded-lg p-4">
              <div className="flex justify-between items-start mb-2">
                <h3 className="font-bold">{sim.name}</h3>
                <span className={`badge ${getStatusColor(sim.status)}`}>
                  {sim.status}
                </span>
              </div>
              <div className="grid grid-cols-3 gap-4 text-sm">
                <div>
                  <div className="opacity-70">Total PnL</div>
                  <div className="font-medium">{formatCurrency(sim.result.total_pnl)}</div>
                </div>
                <div>
                  <div className="opacity-70">Win Rate</div>
                  <div className="font-medium">{formatPercent(sim.result.win_rate)}</div>
                </div>
                <div>
                  <div className="opacity-70">Sharpe Ratio</div>
                  <div className="font-medium">{sim.result.sharpe_ratio.toFixed(2)}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
