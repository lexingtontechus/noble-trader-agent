"use client";
import { useState, useEffect } from "react";
import { Card } from "@/components/layout/Card";
import { listSymbols } from "@/lib/api-mock";

interface SymbolData {
  symbol: string;
  name: string;
  asset_class: string;
  venue: string;
  active: boolean;
}

export function SymbolsPage() {
  const [symbols, setSymbols] = useState<SymbolData[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState("");
  const [filterActive, setFilterActive] = useState(false);

  useEffect(() => {
    const fetchSymbols = async () => {
      try {
        const data = await listSymbols({ active_only: filterActive });
        setSymbols(data.symbols);
      } catch (error) {
        console.error("Failed to load symbols:", error);
      } finally {
        setIsLoading(false);
      }
    };

    fetchSymbols();
  }, [filterActive]);

  const filteredSymbols = symbols.filter(symbol =>
    symbol.symbol.toLowerCase().includes(searchTerm.toLowerCase()) ||
    symbol.name.toLowerCase().includes(searchTerm.toLowerCase())
  );

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
        <h1 className="text-2xl font-bold">Symbols</h1>
        <p className="text-base-content opacity-70">Manage and track trading symbols</p>
      </div>

      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-4">
        <div className="form-control">
          <input
            type="text"
            placeholder="Search symbols..."
            className="input input-bordered w-full sm:w-auto"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
        </div>
        <div className="form-control">
          <label className="label cursor-pointer">
            <span className="label-text">Active only</span>
            <input
              type="checkbox"
              className="checkbox"
              checked={filterActive}
              onChange={(e) => setFilterActive(e.target.checked)}
            />
          </label>
        </div>
      </div>

      {/* Symbols Table */}
      <Card title="Symbols">
        <div className="overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Name</th>
                <th>Asset Class</th>
                <th>Venue</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {filteredSymbols.map((symbol) => (
                <tr key={symbol.symbol}>
                  <td className="font-mono">{symbol.symbol}</td>
                  <td>{symbol.name}</td>
                  <td>{symbol.asset_class}</td>
                  <td>{symbol.venue}</td>
                  <td>
                    <span className={`badge ${symbol.active ? 'badge-success' : 'badge-error'}`}>
                      {symbol.active ? 'Active' : 'Inactive'}
                    </span>
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