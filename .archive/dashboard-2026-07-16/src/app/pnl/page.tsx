"use client";

import { useEffect, useState } from "react";
import { Card } from "@/components/layout/Card";
import { getPnLHistory, getTearSheet } from "@/lib/api-mock";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";

interface PnLData {
  date: string;
  pnl: number;
}

interface TearSheet {
  total_value: number;
  day_pnl: number;
  total_pnl: number;
  positions: Array<{
    symbol: string;
    quantity: number;
    price: number;
    value: number;
    pnl: number;
  }>;
}

export default function PnLPage() {
  const [pnlData, setPnlData] = useState<PnLData[]>([]);
  const [tearSheet, setTearSheet] = useState<TearSheet | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [pnlRes, tearRes] = await Promise.all([
          getPnLHistory(90),
          getTearSheet()
        ]);

        setPnlData(pnlRes.history);
        setTearSheet(tearRes);
      } catch (error) {
        console.error("Failed to load PnL data:", error);
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

  const pnlColor = (pnl: number) => pnl >= 0 ? "text-success" : "text-error";

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
        <h1 className="text-2xl font-bold">Profit & Loss</h1>
        <p className="text-base-content opacity-70">Trading performance analysis</p>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card title="Day PnL">
          <div className={`text-2xl font-bold ${pnlColor(tearSheet?.day_pnl || 0)}`}>
            {formatCurrency(tearSheet?.day_pnl || 0)}
          </div>
        </Card>
        <Card title="Total PnL">
          <div className={`text-2xl font-bold ${pnlColor(tearSheet?.total_pnl || 0)}`}>
            {formatCurrency(tearSheet?.total_pnl || 0)}
          </div>
        </Card>
        <Card title="Total Value">
          <div className="text-2xl font-bold">
            {formatCurrency(tearSheet?.total_value || 0)}
          </div>
        </Card>
      </div>

      {/* PnL Chart */}
      <Card title="PnL History (90 Days)">
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={pnlData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date" tick={{ fontSize: 12 }} tickFormatter={(value) => new Date(value).toLocaleDateString()} />
              <YAxis tick={{ fontSize: 12 }} />
              <Tooltip labelFormatter={(value) => `Date: ${new Date(value).toLocaleDateString()}`} formatter={(value: number) => [`$${value.toFixed(2)}`, "PnL"]} />
              <Area type="monotone" dataKey="pnl" stroke="#8884d8" fill="#8884d8" fillOpacity={0.3} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </Card>

      {/* Positions PnL */}
      <Card title="Positions PnL">
        <div className="overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Quantity</th>
                <th>Price</th>
                <th>Value</th>
                <th>PnL</th>
              </tr>
            </thead>
            <tbody>
              {tearSheet?.positions.map((position) => (
                <tr key={position.symbol}>
                  <td className="font-mono">{position.symbol}</td>
                  <td>{position.quantity}</td>
                  <td>{formatCurrency(position.price)}</td>
                  <td>{formatCurrency(position.value)}</td>
                  <td className={pnlColor(position.pnl)}>
                    {position.pnl >= 0 ? '+' : ''}{formatCurrency(position.pnl)}
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
