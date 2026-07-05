import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Card } from "@/components/layout/Card";
import {
  listSymbols,
  addSymbol,
  activateSymbol,
  deactivateSymbol,
  validateSymbol,
  syncSymbols,
} from "@/lib/api";
import { fmtTs, fmtUSD } from "@/lib/format";
import type { SymbolRow } from "@/lib/types";

export function SymbolsPage() {
  const queryClient = useQueryClient();
  const [showAddModal, setShowAddModal] = useState(false);
  const [activeOnly, setActiveOnly] = useState(false);

  const q = useQuery<{ symbols: SymbolRow[] }>({
    queryKey: ["symbols", { activeOnly }],
    queryFn: () => listSymbols({ active_only: activeOnly }),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["symbols"] });
  };

  const addMut = useMutation({
    mutationFn: (body: Record<string, unknown>) => addSymbol(body),
    onSuccess: () => {
      invalidate();
      setShowAddModal(false);
    },
  });

  const activateMut = useMutation({
    mutationFn: (sym: string) => activateSymbol(sym),
    onSuccess: invalidate,
  });

  const deactivateMut = useMutation({
    mutationFn: (sym: string) => deactivateSymbol(sym),
    onSuccess: invalidate,
  });

  const validateMut = useMutation({
    mutationFn: (sym: string) => validateSymbol(sym),
    onSuccess: invalidate,
  });

  const syncMut = useMutation({
    mutationFn: () => syncSymbols(),
    onSuccess: invalidate,
  });

  const symbols = q.data?.symbols ?? [];

  return (
    <>
      <Card
        title="Symbol Registry"
        extra={
          <span>
            {symbols.filter((s) => s.is_active).length} active ·{" "}
            {symbols.filter((s) => !s.is_active).length} inactive ·{" "}
            <a href="/api/symbols" target="_blank" rel="noreferrer" className="link link-hover">
              JSON
            </a>
          </span>
        }
      >
        {/* Toolbar */}
        <div className="flex flex-wrap gap-2 mb-4">
          <button
            className="btn btn-primary btn-sm"
            onClick={() => setShowAddModal(true)}
          >
            + Add Symbol
          </button>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => syncMut.mutate()}
            disabled={syncMut.isPending}
          >
            {syncMut.isPending ? "Syncing…" : "↻ Sync from config"}
          </button>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => symbols.forEach((s) => s.is_active && validateMut.mutate(s.symbol))}
            disabled={validateMut.isPending}
          >
            ✓ Validate all active
          </button>
          <label className="label cursor-pointer gap-2 ml-auto">
            <span className="label-text text-xs">Active only</span>
            <input
              type="checkbox"
              className="toggle toggle-sm toggle-primary"
              checked={activeOnly}
              onChange={(e) => setActiveOnly(e.target.checked)}
            />
          </label>
        </div>

        {q.isLoading && <div className="text-center py-4 text-xs opacity-50">Loading…</div>}
        {q.isError && (
          <div className="alert alert-error">Failed to load symbols.</div>
        )}

        {symbols.length === 0 && !q.isLoading && (
          <div className="alert alert-info bg-base-300 border-base-300">
            <p className="text-base-content/60 italic py-2">
              No symbols in registry. Click "Sync from config" to seed from default.yaml.
            </p>
          </div>
        )}

        {symbols.length > 0 && (
          <div className="overflow-x-auto">
            <table className="table table-zebra table-sm">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Venue</th>
                  <th>Asset Class</th>
                  <th>Active</th>
                  <th>Validated</th>
                  <th>Last Price</th>
                  <th>Added</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {symbols.map((s) => (
                  <tr key={s.symbol} className={!s.is_active ? "opacity-50" : ""}>
                    <td className="font-semibold font-mono">{s.symbol}</td>
                    <td>{s.venue}</td>
                    <td>{s.asset_class}</td>
                    <td>
                      {s.is_active ? (
                        <span className="badge badge-success badge-sm">active</span>
                      ) : (
                        <span className="badge badge-ghost badge-sm">inactive</span>
                      )}
                    </td>
                    <td>
                      {s.validation_status === "ok" ? (
                        <span className="badge badge-success badge-sm" title={s.validation_error || ""}>
                          ok
                        </span>
                      ) : s.validation_status === "failed" ? (
                        <span className="badge badge-error badge-sm" title={s.validation_error || ""}>
                          failed
                        </span>
                      ) : (
                        <span className="badge badge-ghost badge-sm">pending</span>
                      )}
                    </td>
                    <td className="font-mono text-xs">
                      {s.last_price ? fmtUSD(s.last_price) : "—"}
                    </td>
                    <td className="text-xs opacity-60 font-mono">
                      {s.added_at ? fmtTs(s.added_at) : "—"}
                    </td>
                    <td>
                      <div className="flex gap-1">
                        {s.is_active ? (
                          <button
                            className="btn btn-ghost btn-xs"
                            title="Deactivate"
                            onClick={() => {
                              const reason = prompt(`Reason for deactivating ${s.symbol}? (optional)`);
                              if (reason !== null) deactivateMut.mutate(s.symbol);
                            }}
                          >
                            ✗
                          </button>
                        ) : (
                          <button
                            className="btn btn-ghost btn-xs"
                            title="Activate"
                            onClick={() => activateMut.mutate(s.symbol)}
                          >
                            ✓
                          </button>
                        )}
                        <button
                          className="btn btn-ghost btn-xs"
                          title="Validate"
                          onClick={() => validateMut.mutate(s.symbol)}
                        >
                          ⟳
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {showAddModal && (
        <AddSymbolModal
          onClose={() => setShowAddModal(false)}
          onSubmit={(body) => addMut.mutate(body)}
          error={addMut.error ? String(addMut.error) : undefined}
          isPending={addMut.isPending}
        />
      )}
    </>
  );
}

interface AddSymbolModalProps {
  onClose: () => void;
  onSubmit: (body: Record<string, unknown>) => void;
  error?: string;
  isPending: boolean;
}

function AddSymbolModal({ onClose, onSubmit, error, isPending }: AddSymbolModalProps) {
  const [form, setForm] = useState({
    symbol: "",
    venue: "alpaca",
    asset_class: "crypto",
    base_ccy: "",
    quote_ccy: "USD",
    tick_size: "",
    min_notional: "",
    max_leverage: "",
    rationale: "",
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const body: Record<string, unknown> = {
      symbol: form.symbol,
      venue: form.venue,
      asset_class: form.asset_class,
      quote_ccy: form.quote_ccy,
    };
    if (form.base_ccy) body.base_ccy = form.base_ccy;
    if (form.tick_size) body.tick_size = parseFloat(form.tick_size);
    if (form.min_notional) body.min_notional = parseFloat(form.min_notional);
    if (form.max_leverage) body.max_leverage = parseFloat(form.max_leverage);
    if (form.rationale) body.rationale = form.rationale;
    onSubmit(body);
  };

  return (
    <dialog className="modal modal-open" onClose={onClose}>
      <div className="modal-box">
        <h3 className="font-bold text-lg mb-4">Add Symbol</h3>
        <form onSubmit={handleSubmit}>
          <div className="form-control mb-3">
            <label className="label"><span className="label-text">Symbol *</span></label>
            <input
              type="text"
              required
              placeholder="e.g. BTC/USD, SOL/USD, BTC-PERP"
              className="input input-bordered input-sm w-full font-mono"
              value={form.symbol}
              onChange={(e) => setForm({ ...form, symbol: e.target.value })}
            />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div className="form-control mb-3">
              <label className="label"><span className="label-text">Venue *</span></label>
              <select
                className="select select-bordered select-sm w-full"
                value={form.venue}
                onChange={(e) => setForm({ ...form, venue: e.target.value })}
              >
                <option value="alpaca">alpaca</option>
                <option value="hyperliquid">hyperliquid</option>
              </select>
            </div>
            <div className="form-control mb-3">
              <label className="label"><span className="label-text">Asset Class *</span></label>
              <select
                className="select select-bordered select-sm w-full"
                value={form.asset_class}
                onChange={(e) => setForm({ ...form, asset_class: e.target.value })}
              >
                <option value="crypto">crypto</option>
                <option value="equities">equities</option>
                <option value="commodities">commodities</option>
                <option value="forex">forex</option>
              </select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div className="form-control mb-3">
              <label className="label"><span className="label-text">Base CCY</span></label>
              <input
                type="text"
                placeholder="auto-derived"
                className="input input-bordered input-sm w-full font-mono"
                value={form.base_ccy}
                onChange={(e) => setForm({ ...form, base_ccy: e.target.value })}
              />
            </div>
            <div className="form-control mb-3">
              <label className="label"><span className="label-text">Quote CCY</span></label>
              <input
                type="text"
                className="input input-bordered input-sm w-full font-mono"
                value={form.quote_ccy}
                onChange={(e) => setForm({ ...form, quote_ccy: e.target.value })}
              />
            </div>
          </div>
          <div className="grid grid-cols-3 gap-2">
            <div className="form-control mb-3">
              <label className="label"><span className="label-text">Tick Size</span></label>
              <input
                type="number"
                step="any"
                placeholder="optional"
                className="input input-bordered input-sm w-full"
                value={form.tick_size}
                onChange={(e) => setForm({ ...form, tick_size: e.target.value })}
              />
            </div>
            <div className="form-control mb-3">
              <label className="label"><span className="label-text">Min Notional</span></label>
              <input
                type="number"
                step="any"
                placeholder="optional"
                className="input input-bordered input-sm w-full"
                value={form.min_notional}
                onChange={(e) => setForm({ ...form, min_notional: e.target.value })}
              />
            </div>
            <div className="form-control mb-3">
              <label className="label"><span className="label-text">Max Leverage</span></label>
              <input
                type="number"
                step="any"
                placeholder="optional"
                className="input input-bordered input-sm w-full"
                value={form.max_leverage}
                onChange={(e) => setForm({ ...form, max_leverage: e.target.value })}
              />
            </div>
          </div>
          <div className="form-control mb-3">
            <label className="label"><span className="label-text">Rationale</span></label>
            <input
              type="text"
              placeholder="Why is this symbol being added?"
              className="input input-bordered input-sm w-full"
              value={form.rationale}
              onChange={(e) => setForm({ ...form, rationale: e.target.value })}
            />
          </div>

          {error && (
            <div className="alert alert-error text-xs py-2 mb-3">{error}</div>
          )}

          <div className="modal-action">
            <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary btn-sm" disabled={isPending}>
              {isPending ? "Adding…" : "Add Symbol"}
            </button>
          </div>
        </form>
      </div>
      <form method="dialog" className="modal-backdrop" onClick={onClose}>
        <button>close</button>
      </form>
    </dialog>
  );
}
