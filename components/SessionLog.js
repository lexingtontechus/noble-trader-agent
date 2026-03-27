'use client'
import { useState, useEffect, useCallback } from 'react'
import Badge from './ui/Badge'
import { useSupabase, PAGE_SIZE } from '../hooks/useSupabase'
import styles from './SessionLog.module.css'

export default function SessionLog({ refreshTrigger }) {
  const { fetchSessionLog } = useSupabase()
  const [rows, setRows]       = useState([])
  const [page, setPage]       = useState(1)
  const [total, setTotal]     = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const load = useCallback(async (p) => {
    setLoading(true)
    setError(null)
    const { data, count, error: err } = await fetchSessionLog({ page: p })
    setLoading(false)
    if (err) { setError('Failed to load session log.'); return }
    setRows(data)
    setTotal(count)
  }, [fetchSessionLog])

  useEffect(() => { load(page) }, [page, refreshTrigger, load])

  const goTo = (p) => { if (p >= 1 && p <= totalPages) setPage(p) }

  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <div className={styles.headerLeft}>
          <span className={styles.title}>Session Log</span>
          <span className={styles.headerSub}>last 3 days</span>
        </div>
        <div className={styles.headerRight}>
          <span className={styles.badge}>{total} trade{total !== 1 ? 's' : ''}</span>
          <button className={styles.refreshBtn} onClick={() => load(page)} disabled={loading} title="Refresh">
            <RefreshIcon spinning={loading} />
          </button>
        </div>
      </div>

      <div className={styles.tableWrap}>
        {error ? (
          <div className={styles.notice}>{error}</div>
        ) : (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>#</th><th>Time</th><th>Dir</th><th>p_win</th>
                <th>regime</th><th>contracts</th><th>risk $</th><th>edge</th><th>status</th>
              </tr>
            </thead>
            <tbody>
              {loading && rows.length === 0 ? (
                <tr><td colSpan={9} className={styles.empty}><LoadingDots /></td></tr>
              ) : rows.length === 0 ? (
                <tr><td colSpan={9} className={styles.empty}>No trades in the last 3 days</td></tr>
              ) : (
                rows.map((row, idx) => (
                  <LogRow key={row.id} row={row} rowNum={(page - 1) * PAGE_SIZE + idx + 1} />
                ))
              )}
            </tbody>
          </table>
        )}
      </div>

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

function LogRow({ row, rowNum }) {
  const edgeColor = row.expected_edge > 0 ? 'var(--green)' : 'var(--red)'
  const dt   = new Date(row.created_at)
  const time = dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  const date = dt.toLocaleDateString([], { month: 'short', day: 'numeric' })
  return (
    <tr>
      <td>{rowNum}</td>
      <td><span className={styles.timeCell}><span>{date}</span><span className={styles.timeSub}>{time}</span></span></td>
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

function RefreshIcon({ spinning }) {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
      style={{ animation: spinning ? 'spin 1s linear infinite' : 'none' }}>
      <polyline points="23 4 23 10 17 10" />
      <polyline points="1 20 1 14 7 14" />
      <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
    </svg>
  )
}
