"use client";

import { useEffect, useState } from "react";
import { Card } from "@/components/layout/Card";
import { getPortfolioMetrics, getPortfolioExposure } from "@/lib/api-mock";
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from "recharts";

interface PortfolioMetrics {
  total_value: number;
  day_pnl: number;
  total_pnl: number;
  return_pct: number;
  positions: Array<{
    symbol: string;
    quantity: number;
    price: number;
    value: number;
    pnl: number;
  }>;
  risk_metrics: {
    sharpe_ratio: number;
    max_drawdown: number;
    volatility: number;
  };
}

interface ExposureData {
  asset_class: string;
  value: number;
  percentage: number;
}

const COLORS = ['#0088FE', '#00C49F', '#FFBB28', '#FF8042', '#8884d8'];

export default function PortfolioPage() {
  const [metrics, setMetrics] = useState<PortfolioMetrics | null>(null);
  const [exposure, setExposure] = useState<ExposureData[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [portfolioRes] = await Promise.all([
          getPortfolioMetrics(),
          getPortfolioExposure()
        ]);

        setMetrics(portfolioRes);
        setExposure([
          { asset_class: 'Stocks', value: 75000, percentage: 60 },
          { asset_class: 'Crypto', value: 35000, percentage: 28 },
          { asset_class: 'Options', value: 10000, percentage: 8 },
          { asset_class: 'Cash', value: 5430.50, percentage: 4 }
        ]);
      } catch (error) {
        console.error("Failed to load portfolio data:", error);
      } finally {
        setIsLoading(false);
      }
    };

    fetchData();
  }, []);

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
        <h1 className="text-2xl font-bold">Portfolio</h1>
        <p className="text-base-content opacity-70">Complete portfolio overview and analysis</p>
      </div>

      {/* Portfolio Summary */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card title="Total Value">
          <div className="text-2xl font-bold">
            {formatCurrency(metrics?.total_value || 0)}
          </div>
        </Card>
        <Card title="Day PnL">
          <div className="text-2xl font-bold text-success">
            {formatCurrency(metrics?.day_pnl || 0)}
          </div>
        </Card>
        <Card title="Total PnL">
          <div className="text-2xl font-bold text-success">
            {formatCurrency(metrics?.total_pnl || 0)}
          </div>
        </Card>
        <Card title="Return">
          <div className="text-2xl font-bold text-success">
            {formatPercent(metrics?.return_pct || 0)}
          </div>
        </Card>
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Asset Allocation */}
        <Card title="Asset Allocation">
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={exposure}
                  cx="50%"
                  cy="50%"
                  labelLine={false}
                  label={({ asset_class, percentage }) => `${asset_class} ${percentage}%`}
                  outerRadius={80}
                  fill="#8884d8"
                  dataKey="value"
                >
                  {exposure.map((_entry, index) => (
                    <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip formatter={(value: number) => formatCurrency(value)} />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </Card>

        {/* Risk Metrics */}
        <Card title="Risk Metrics">
          <div className="space-y-4">
            <div className="flex justify-between">
              <span>Sharpe Ratio</span>
              <span className="font-bold">{metrics?.risk_metrics.sharpe_ratio.toFixed(2)}</span>
            </div>
            <div className="flex justify-between">
              <span>Max Drawdown</span>
              <span className="font-bold text-error">
                {formatPercent(metrics?.risk_metrics.max_drawdown || 0)}
              </span>
            </div>
            <div className="flex justify-between">
              <span>Volatility</span>
              <span className="font-bold">{formatPercent(metrics?.risk_metrics.volatility || 0)}</span>
            </div>
          </div>
        </Card>
      </div>

      {/* Positions Table */}
      <Card title="Positions">
        <div className="overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Quantity</th>
                <th>Avg Price</th>
                <th>Current Price</th>
                <th>Value</th>
                <th>PnL</th>
                <th>PnL %</th>
              </tr>
            </thead>
            <tbody>
              {metrics?.positions.map((position) => (
                <tr key={position.symbol}>
                  <td className="font-mono">{position.symbol}</td>
                  <td>{position.quantity}</td>
                  <td>{formatCurrency(position.price * 0.98)}</td>
                  <td>{formatCurrency(position.price)}</td>
                  <td>{formatCurrency(position.value)}</td>
                  <td className={position.pnl >= 0 ? "text-success" : "text-error"}>
                    {position.pnl >= 0 ? '+' : ''}{formatCurrency(position.pnl)}
                  </td>
                  <td className={position.pnl >= 0 ? "text-success" : "text-error"}>
                    {((position.pnl / position.value) * 100).toFixed(2)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
