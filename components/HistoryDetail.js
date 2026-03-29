'use client'
import FactorBar from './ui/FactorBar'
import Badge from './ui/Badge'
import styles from './HistoryDetail.module.css'

const FACTOR_DEFS = [
  { key: 'masaniello_factor', name: 'masaniello', max: 1.5, color: 'var(--accent)' },
  { key: 'quality_factor',    name: 'quality',    max: 2.0, color: 'var(--blue)' },
  { key: 'drawdown_factor',   name: 'drawdown',   max: 1.0, color: 'var(--green)' },
  { key: 'volatility_factor', name: 'volatility', max: 1.5, color: 'var(--amber)' },
]

const STATE_COLORS = {
  bull: '#22c55e', bear: '#ef4444', range: '#3b82f6', volatile: '#f59e0b',
}

export default function HistoryDetail({ logRow, factor, loading, onClose }) {
  const dt   = new Date(logRow.created_at)
  const date = dt.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })
  const time = dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })

  return (
    <div className={styles.panel}>
      {/* ── Panel header ── */}
      <div className={styles.header}>
        <div className={styles.headerLeft}>
          <span className={styles.title}>Trade Detail</span>
          <span className={styles.headerTime}>{date} · {time}</span>
        </div>
        <button className={styles.closeBtn} onClick={onClose} title="Close detail">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>

      <div className={styles.body}>
        {loading ? (
          <div className={styles.loadingWrap}><LoadingDots /></div>
        ) : (
          <>
            {/* ── Decision hero ── */}
            <div className={`${styles.decisionBanner} ${logRow.allowed ? styles.decisionAllowed : styles.decisionBlocked}`}>
              <span className={styles.decisionLabel}>
                {logRow.allowed ? 'TRADE ALLOWED' : 'TRADE BLOCKED'}
              </span>
              <div className={styles.decisionMeta}>
                <Badge variant={logRow.direction}>{logRow.direction.toUpperCase()}</Badge>
                <span>{logRow.contracts} contract{logRow.contracts !== 1 ? 's' : ''}</span>
                {logRow.risk_amount > 0 && <span>Risk ${Number(logRow.risk_amount).toFixed(2)}</span>}
              </div>
            </div>

            {/* ── Summary metrics ── */}
            <div className={styles.metricsGrid}>
              <Metric label="p_win"    value={Number(logRow.p_win).toFixed(2)} />
              <Metric label="edge"     value={`${Number(logRow.expected_edge).toFixed(3)}R`} color={logRow.expected_edge > 0 ? 'var(--green)' : 'var(--red)'} />
              <Metric label="regime"   value={logRow.regime_label.replace('_', ' ')} />
              <Metric label="risk $"   value={logRow.risk_amount > 0 ? `$${Number(logRow.risk_amount).toFixed(2)}` : '—'} />
            </div>

            {factor ? (
              <>
                {/* ── Factor bars ── */}
                <p className={styles.sectionLabel}>Factor Breakdown</p>
                <div className={styles.factorGrid}>
                  {FACTOR_DEFS.map(({ key, name, max, color }) => (
                    <FactorBar
                      key={key}
                      name={name}
                      value={Number(factor[key])}
                      max={max}
                      color={color}
                    />
                  ))}
                </div>

                {/* ── Markov state probs ── */}
                {factor.markov_bull != null && (
                  <>
                    <p className={styles.sectionLabel}>Markov States</p>
                    <div className={styles.statesGrid}>
                      {[
                        { key: 'markov_bull',     label: 'bull',     color: STATE_COLORS.bull },
                        { key: 'markov_bear',     label: 'bear',     color: STATE_COLORS.bear },
                        { key: 'markov_range',    label: 'range',    color: STATE_COLORS.range },
                        { key: 'markov_volatile', label: 'volatile', color: STATE_COLORS.volatile },
                      ].map(({ key, label, color }) => {
                        const pct = Number(factor[key]) * 100
                        return (
                          <div key={key} className={styles.stateTile}>
                            <div className={styles.stateBar}>
                              <div className={styles.stateBarFill} style={{ width: `${pct}%`, background: color }} />
                            </div>
                            <div className={styles.stateMeta}>
                              <span className={styles.stateLabel}>{label}</span>
                              <span className={styles.stateVal} style={{ color }}>{pct.toFixed(1)}%</span>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </>
                )}

                {/* ── Input context ── */}
                <p className={styles.sectionLabel}>Trade Context</p>
                <div className={styles.contextGrid}>
                  <ContextRow label="equity"         value={`$${Number(factor.equity).toLocaleString()}`} />
                  <ContextRow label="stop distance"  value={`$${Number(factor.stop_distance).toFixed(2)}`} />
                  <ContextRow label="reward / risk"  value={`${Number(factor.reward_risk).toFixed(2)}R`} />
                  <ContextRow label="atr baseline"   value={Number(factor.atr_baseline).toFixed(2)} />
                  <ContextRow label="atr current"    value={Number(factor.atr_current).toFixed(2)} />
                  <ContextRow label="drawdown"       value={`${(Number(factor.current_drawdown) * 100).toFixed(2)}%`} />
                  <ContextRow label="state conf."    value={Number(factor.state_confidence).toFixed(2)} />
                  <ContextRow label="regime quality" value={Number(factor.regime_quality).toFixed(2)} />
                </div>

                {/* ── Batch state ── */}
                <p className={styles.sectionLabel}>Batch State</p>
                <div className={styles.contextGrid}>
                  <ContextRow label="batch"          value={`${factor.trade_index}/${factor.batch_size}`} />
                  <ContextRow label="target wins"    value={factor.target_wins} />
                  <ContextRow label="wins so far"    value={factor.wins_so_far} />
                  <ContextRow label="losses so far"  value={factor.losses_so_far} />
                </div>

                {/* ── Risk config ── */}
                <p className={styles.sectionLabel}>Risk Config</p>
                <div className={styles.contextGrid}>
                  <ContextRow label="base risk"  value={`${(Number(factor.base_risk) * 100).toFixed(2)}%`} />
                  <ContextRow label="min risk"   value={`${(Number(factor.min_risk) * 100).toFixed(2)}%`} />
                  <ContextRow label="max risk"   value={`${(Number(factor.max_risk) * 100).toFixed(2)}%`} />
                  <ContextRow label="kelly"      value={factor.use_kelly ? `${Number(factor.kelly_fraction).toFixed(2)} frac` : 'off'} />
                </div>

                {/* ── Reason ── */}
                <div className={`${styles.reasonBox} ${logRow.allowed ? styles.reasonOk : styles.reasonBlocked}`}>
                  {factor.reason}
                </div>
              </>
            ) : (
              <div className={styles.noDetail}>
                No detailed factor data linked to this log entry.
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function Metric({ label, value, color }) {
  return (
    <div className={styles.metric}>
      <span className={styles.metricLabel}>{label}</span>
      <span className={styles.metricValue} style={color ? { color } : {}}>{value}</span>
    </div>
  )
}

function ContextRow({ label, value }) {
  return (
    <div className={styles.contextRow}>
      <span className={styles.contextLabel}>{label}</span>
      <span className={styles.contextValue}>{value}</span>
    </div>
  )
}

function LoadingDots() {
  return (
    <span className={styles.loadingDots}><span /><span /><span /></span>
  )
}
