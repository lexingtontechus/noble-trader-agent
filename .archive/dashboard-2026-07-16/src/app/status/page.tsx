"use client";

import { Card } from "@/components/layout/Card";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";

interface ServiceStatus {
  name: string;
  status: "healthy" | "warning" | "error";
  last_check: string;
  uptime: string;
}

export default function StatusPage() {
  const [services] = useState<ServiceStatus[]>([
    { name: "Hermes Core", status: "healthy", last_check: new Date().toISOString(), uptime: "3d 14h 32m" },
    { name: "API Gateway", status: "healthy", last_check: new Date().toISOString(), uptime: "3d 14h 32m" },
    { name: "Database", status: "healthy", last_check: new Date().toISOString(), uptime: "3d 14h 32m" },
    { name: "Cache (Redis)", status: "healthy", last_check: new Date().toISOString(), uptime: "7d 5h 12m" },
    { name: "External APIs", status: "warning", last_check: new Date().toISOString(), uptime: "2d 8h 45m" },
  ]);

  const pnlData = Array.from({ length: 30 }, (_, i) => ({
    date: new Date(Date.now() - (30 - i) * 24 * 60 * 60 * 1000).toLocaleDateString(),
    pnl: Math.random() * 2000 - 1000,
  }));

  const getStatusColor = (status: string) => {
    switch (status) {
      case "healthy": return "text-success";
      case "warning": return "text-warning";
      case "error": return "text-error";
      default: return "text-base-content";
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">System Status</h1>
        <p className="text-base-content opacity-70">All systems operational</p>
      </div>

      {/* Service Status Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {services.map((service) => (
          <Card key={service.name} title={service.name}>
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-sm opacity-70">Status</span>
                <span className={`font-medium ${getStatusColor(service.status)}`}>
                  {service.status}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm opacity-70">Last Check</span>
                <span className="text-sm">
                  {new Date(service.last_check).toLocaleTimeString()}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm opacity-70">Uptime</span>
                <span className="text-sm font-mono">{service.uptime}</span>
              </div>
            </div>
          </Card>
        ))}
      </div>

      {/* PnL Chart */}
      <Card title="PnL Overview (30 Days)">
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={pnlData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date" tick={{ fontSize: 12 }} tickFormatter={(value) => new Date(value).toLocaleDateString()} />
              <YAxis tick={{ fontSize: 12 }} />
              <Tooltip labelFormatter={(value) => `Date: ${value}`} formatter={(value: number) => [`$${value.toFixed(2)}`, "PnL"]} />
              <Area type="monotone" dataKey="pnl" stroke="#8884d8" fill="#8884d8" fillOpacity={0.3} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </Card>
    </div>
  );
}
