"use client";
import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
//import AppHeader from './AppHeader'
import HistoryTable from "./HistoryTable";
import HistoryDetail from "./HistoryDetail";
import { useSupabase, DATE_FILTERS, PAGE_SIZE } from "../hooks/useSupabase";
import styles from "./HistoryClient.module.css";

export default function HistoryClient() {
  const { fetchSessionLogFiltered, fetchFactorById } = useSupabase();

  // ── Filter / pagination state ──
  const [dateFilter, setDateFilter] = useState("3d");
  const [page, setPage] = useState(1);

  // ── Data state ──
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // ── Selected row detail ──
  const [selectedRow, setSelectedRow] = useState(null); // session_log row
  const [selectedFactor, setSelectedFactor] = useState(null); // trade_sizing_factors row
  const [detailLoading, setDetailLoading] = useState(false);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // ── Load table rows ──
  const load = useCallback(
    async (p, filter) => {
      setLoading(true);
      setError(null);
      const {
        data,
        count,
        error: err,
      } = await fetchSessionLogFiltered({ page: p, dateFilter: filter });
      setLoading(false);
      if (err) {
        setError("Failed to load history.");
        return;
      }
      setRows(data);
      setTotal(count);
    },
    [fetchSessionLogFiltered]
  );

  useEffect(() => {
    load(page, dateFilter);
  }, [page, dateFilter, load]);

  // Reset to page 1 when filter changes
  const handleFilterChange = (val) => {
    setDateFilter(val);
    setPage(1);
    setSelectedRow(null);
    setSelectedFactor(null);
  };

  // ── Select a row — fetch full factor detail ──
  const handleSelectRow = useCallback(
    async (row) => {
      if (selectedRow?.id === row.id) {
        // Deselect
        setSelectedRow(null);
        setSelectedFactor(null);
        return;
      }
      setSelectedRow(row);
      setSelectedFactor(null);
      if (row.sizing_factor_id) {
        setDetailLoading(true);
        const { data } = await fetchFactorById(row.sizing_factor_id);
        setDetailLoading(false);
        setSelectedFactor(data);
      }
    },
    [selectedRow, fetchFactorById]
  );

  const goTo = (p) => {
    if (p >= 1 && p <= totalPages) setPage(p);
  };

  // Summary stats for selected filter window
  const allowedCount = rows.filter((r) => r.allowed).length;
  const blockedCount = rows.filter((r) => !r.allowed).length;
  const avgEdge =
    rows.length > 0
      ? rows.reduce((s, r) => s + Number(r.expected_edge), 0) / rows.length
      : null;

  return (
    <div className={styles.app}>
      {/*<AppHeader />*/}

      {/* ── Page header ── */}
      <div className={styles.pageHeader}>
        <div className={styles.pageHeaderLeft}>
          <Link href="/" className={styles.backLink}>
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <polyline points="15 18 9 12 15 6" />
            </svg>
            Dashboard
          </Link>
          <div className={styles.pageTitleGroup}>
            <h2 className={styles.pageTitle}>Trade History</h2>
            <span className={styles.pageSubtitle}>
              session log with full factor detail
            </span>
          </div>
        </div>

        {/* ── Date filter pills ── */}
        <div className={styles.filterGroup}>
          {DATE_FILTERS.map((f) => (
            <button
              key={f.value}
              className={`${styles.filterBtn} ${
                dateFilter === f.value ? styles.filterBtnActive : ""
              }`}
              onClick={() => handleFilterChange(f.value)}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {/* ── Summary stat chips ── */}
      <div className={styles.statsRow}>
        <StatChip label="total" value={total} />
        <StatChip label="allowed" value={allowedCount} color="green" />
        <StatChip label="blocked" value={blockedCount} color="red" />
        {avgEdge !== null && (
          <StatChip
            label="avg edge"
            value={`${avgEdge.toFixed(3)}R`}
            color={avgEdge > 0 ? "green" : "red"}
          />
        )}
        <StatChip label="page" value={`${page} / ${totalPages}`} />
      </div>

      {/* ── Main layout: table + detail panel ── */}
      <div
        className={`${styles.mainGrid} ${selectedRow ? styles.withDetail : ""}`}
      >
        <HistoryTable
          rows={rows}
          loading={loading}
          error={error}
          page={page}
          totalPages={totalPages}
          selectedId={selectedRow?.id}
          onSelectRow={handleSelectRow}
          goTo={goTo}
          total={total}
        />

        {selectedRow && (
          <HistoryDetail
            logRow={selectedRow}
            factor={selectedFactor}
            loading={detailLoading}
            onClose={() => {
              setSelectedRow(null);
              setSelectedFactor(null);
            }}
          />
        )}
      </div>
    </div>
  );
}

function StatChip({ label, value, color }) {
  const colorMap = { green: "var(--green)", red: "var(--red)" };
  return (
    <div className={styles.statChip}>
      <span className={styles.statLabel}>{label}</span>
      <span
        className={styles.statValue}
        style={color ? { color: colorMap[color] } : {}}
      >
        {value}
      </span>
    </div>
  );
}
