'use client'
import Badge from './ui/Badge'
import { PAGE_SIZE } from '../hooks/useSupabase'
import styles from './HistoryTable.module.css'

export default function HistoryTable({
  rows, loading, error, page, totalPages, selectedId,
  onSelectRow, goTo, total,
}) {
  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <span className={styles.title}>Session Log</span>
        <span className={styles.badge}>{total} row{total !== 1 ? 's' : ''}</span>
      </div>

      <div className={styles.tableWrap}>
        {error ? (
          <div className={styles.notice}>{error}</div>
        ) : (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>#</th>
                <th>Date</th>
                <th>Time</th>
                <th>Dir</th>
                <th>p_win</th>
                <th>regime</th>
                <th>contracts</th>
                <th>risk $</th>
                <th>edge</th>
                <th>status</th>
              </tr>
            </thead>
            <tbody>
              {loading && rows.length === 0 ? (
                <tr><td colSpan={10} className={styles.empty}><LoadingDots /></td></tr>
              ) : rows.length === 0 ? (
                <tr><td colSpan={10} className={styles.empty}>No trades found in this period</td></tr>
              ) : (
                rows.map((row, idx) => (
                  <HistoryRow
                    key={row.id}
                    row={row}
                    rowNum={(page - 1) * PAGE_SIZE + idx + 1}
                    isSelected={selectedId === row.id}
                    onClick={() => onSelectRow(row)}
                  />
                ))
              )}
            </tbody>
          </table>
        )}
      </div>

      {/* ── Pagination ── */}
      {totalPages > 1 && (
        <div className={styles.pagination}>
          <button className={styles.pageBtn} onClick={() => goTo(1)} disabled={page === 1}>«</button>
          <button className={styles.pageBtn} onClick={() => goTo(page - 1)} disabled={page === 1}>‹</button>
          <PageNumbers current={page} total={totalPages} goTo={goTo} />
          <button className={styles.pageBtn} onClick={() => goTo(page + 1)} disabled={page === totalPages}>›</button>
          <button className={styles.pageBtn} onClick={() => goTo(totalPages)} disabled={page === totalPages}>»</button>
          <span className={styles.pageInfo}>{page} / {totalPages} · {total} rows</span>
        </div>
      )}
    </div>
  )
}

function HistoryRow({ row, rowNum, isSelected, onClick }) {
  const edgeColor = row.expected_edge > 0 ? 'var(--green)' : 'var(--red)'
  const dt   = new Date(row.created_at)
  const date = dt.toLocaleDateString([], { month: 'short', day: 'numeric', year: '2-digit' })
  const time = dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

  return (
    <tr
      className={`${styles.row} ${isSelected ? styles.rowSelected : ''}`}
      onClick={onClick}
    >
      <td className={styles.numCell}>{rowNum}</td>
      <td>{date}</td>
      <td className={styles.timeCell}>{time}</td>
      <td><Badge variant={row.direction}>{row.direction.toUpperCase()}</Badge></td>
      <td>{Number(row.p_win).toFixed(2)}</td>
      <td>{row.regime_label.replace('_', ' ')}</td>
      <td>{row.contracts}</td>
      <td>{row.risk_amount > 0 ? `$${Number(row.risk_amount).toFixed(2)}` : '—'}</td>
      <td style={{ color: edgeColor }}>{Number(row.expected_edge).toFixed(3)}R</td>
      <td><Badge variant={row.allowed ? 'ok' : 'blocked'}>{row.allowed ? 'OK' : 'BLOCKED'}</Badge></td>
    </tr>
  )
}

function PageNumbers({ current, total, goTo }) {
  const range = []
  const delta = 2
  const left  = Math.max(1, current - delta)
  const right = Math.min(total, current + delta)
  if (left > 1) range.push(1)
  if (left > 2) range.push('…')
  for (let i = left; i <= right; i++) range.push(i)
  if (right < total - 1) range.push('…')
  if (right < total) range.push(total)
  return (
    <>
      {range.map((p, i) =>
        p === '…'
          ? <span key={`e${i}`} className={styles.ellipsis}>…</span>
          : <button key={p} className={`${styles.pageBtn} ${p === current ? styles.pageBtnActive : ''}`} onClick={() => goTo(p)}>{p}</button>
      )}
    </>
  )
}

function LoadingDots() {
  return <span className={styles.loadingDots}><span /><span /><span /></span>
}
