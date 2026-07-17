"use client";
import { useEffect, useState } from "react";
import { Card } from "@/components/layout/Card";
import { useAuth } from "@/lib/auth-simple";
import { getPortfolioMetrics, getPnLHistory } from "@/lib/api-mock";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";

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

export function DashboardPage() {
  const { user } = useAuth();
  const [metrics, setMetrics] = useState<PortfolioMetrics | null>(null);
  const [pnlData, setPnlData] = useState<Array<{ date: string; pnl: number }>>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [portfolioRes, pnlRes] = await Promise.all([
          getPortfolioMetrics(),
          getPnLHistory(30)
        ]);
        
        setMetrics(portfolioRes);
        setPnlData(pnlRes.history);
      } catch (error) {
        console.error("Failed to load dashboard data:", error);
      } finally {
        setIsLoading(false);
      }
    };

    fetchData();
  }, []);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <span className="loading loading-spinner loading-lg text-primary" />
      </div>
    );
  }

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

  const pnlColor = (pnl: number) => pnl >= 0 ? "text-success" : "text-error";

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">Welcome back, {user?.username}</h1>
        <p className="text-base-content opacity-70">Here's your trading portfolio overview</p>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card title="Total Value">
          <div className="text-2xl font-bold">
            {formatCurrency(metrics?.total_value || 0)}
          </div>
        </Card>
        <Card title="Day PnL">
          <div className={`text-2xl font-bold ${pnlColor(metrics?.day_pnl || 0)}`}>
            {formatCurrency(metrics?.day_pnl || 0)}
          </div>
        </Card>
        <Card title="Total PnL">
          <div className={`text-2xl font-bold ${pnlColor(metrics?.total_pnl || 0)}`}>
            {formatCurrency(metrics?.total_pnl || 0)}
          </div>
        </Card>
        <Card title="Return">
          <div className={`text-2xl font-bold ${pnlColor(metrics?.return_pct || 0)}`}>
            {formatPercent(metrics?.return_pct || 0)}
          </div>
        </Card>
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card title="PnL History">
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={pnlData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis 
                  dataKey="date" 
                  tick={{ fontSize: 12 }}
                  tickFormatter={(value) => new Date(value).toLocaleDateString()}
                />
                <YAxis tick={{ fontSize: 12 }} />
                <Tooltip 
                  labelFormatter={(value) => `Date: ${new Date(value).toLocaleDateString()}`}
                  formatter={(value: number) => [`$${value.toFixed(2)}`, "PnL"]}
                />
                <Area 
                  type="monotone" 
                  dataKey="pnl" 
                  stroke="#8884d8" 
                  fill="#8884d8" 
                  fillOpacity={0.3}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card title="Positions">
          <div className="space-y-4">
            {metrics?.positions.map((position) => (
              <div key={position.symbol} className="flex items-center justify-between py-2 border-b last:border-0">
                <div className="flex items-center space-x-3">
                  <div className="font-medium">{position.symbol}</div>
                  <div className="text-sm opacity-70">
                    {position.quantity} @ {formatCurrency(position.price)}
                  </div>
                </div>
                <div className="text-right">
                  <div className="font-medium">{formatCurrency(position.value)}</div>
                  <div className={`text-sm ${pnlColor(position.pnl)}`}>
                    {position.pnl >= 0 ? '+' : ''}{formatCurrency(position.pnl)}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      {/* Risk Metrics */}
      <Card title="Risk Metrics">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="text-center">
            <div className="text-sm opacity-70">Sharpe Ratio</div>
            <div className="text-xl font-bold">{metrics?.risk_metrics.sharpe_ratio.toFixed(2)}</div>
          </div>
          <div className="text-center">
            <div className="text-sm opacity-70">Max Drawdown</div>
            <div className="text-xl font-bold text-error">
              {formatPercent(metrics?.risk_metrics.max_drawdown || 0)}
            </div>
          </div>
          <div className="text-center">
            <div className="text-sm opacity-70">Volatility</div>
            <div className="text-xl font-bold">{formatPercent(metrics?.risk_metrics.volatility || 0)}</div>
          </div>
        </div>
      </Card>
    </div>
  );
}