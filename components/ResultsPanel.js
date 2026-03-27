'use client'
import FactorBar from './ui/FactorBar'
import styles from './ResultsPanel.module.css'

const FACTOR_DEFS = [
  { key: 'masaniello_factor', name: 'masaniello', max: 1.5, color: 'var(--accent)' },
  { key: 'quality_factor',    name: 'quality',    max: 2.0, color: 'var(--blue)' },
  { key: 'drawdown_factor',   name: 'drawdown',   max: 1.0, color: 'var(--green)' },
  { key: 'volatility_factor', name: 'volatility', max: 1.5, color: 'var(--amber)' },
]

export default function ResultsPanel({ result, params }) {
  if (!result) return <EmptyState />

  const { allowed, reason, contracts, units, risk_fraction, risk_amount, expected_edge } = result

  const verdictText = allowed
    ? `TRADE ALLOWED — ${params.direction.toUpperCase()} ${contracts} contract${contracts !== 1 ? 's' : ''} · Risk $${risk_amount.toFixed(2)} (${(risk_fraction * 100).toFixed(3)}%) · Edge ${expected_edge.toFixed(3)}R`
    : `TRADE BLOCKED — ${reason}`

  return (
    <div className={styles.card}>
      <div className={styles.heroGrid}>
        <HeroMetric label="Decision" value={allowed ? 'ALLOWED' : 'BLOCKED'} valueClass={allowed ? styles.green : styles.red} sub={reason} />
        <HeroMetric label="Contracts" value={contracts} valueClass={styles.accent} sub={units > 0 ? `${units.toFixed(2)} units` : '— units'} />
        <HeroMetric label="Risk Amount" value={risk_amount > 0 ? `$${risk_amount.toFixed(2)}` : '—'} sub={risk_fraction > 0 ? `${(risk_fraction * 100).toFixed(3)}% equity` : '—'} />
        <HeroMetric
          label="Expected Edge"
          value={expected_edge !== 0 ? `${expected_edge.toFixed(3)}R` : '—'}
          valueClass={expected_edge > 0 ? styles.green : expected_edge < 0 ? styles.red : ''}
          sub="E = p·R − (1−p)"
        />
      </div>

      <div className={styles.body}>
        <div>
          <p className={styles.sectionLabel}>Factor Breakdown</p>
          <div className={styles.factorGrid}>
            {FACTOR_DEFS.map(({ key, name, max, color }) => (
              <FactorBar key={key} name={name} value={result[key]} max={max} color={color} />
            ))}
          </div>
          <div className={styles.divider} />
          <div className={styles.chips}>
            <InfoChip label="batch"    value={`${params.trade_index}/${params.batch_size}`} />
            <InfoChip label="progress" value={`${params.wins_so_far}W ${params.losses_so_far}L / ${params.target_wins} target`} />
            <InfoChip label="regime"   value={params.regime_label.replace('_', ' ')} />
            <InfoChip label="dir"      value={params.direction.toUpperCase()} />
          </div>
        </div>
        <div>
          <p className={styles.sectionLabel}>Risk Engine Verdict</p>
          <div className={`${styles.verdict} ${allowed ? styles.verdictOk : styles.verdictBlocked}`}>
            {verdictText}
          </div>
        </div>
      </div>
    </div>
  )
}

function HeroMetric({ label, value, valueClass = '', sub }) {
  return (
    <div className={styles.heroMetric}>
      <p className={styles.metricLabel}>{label}</p>
      <p className={`${styles.metricValue} ${valueClass}`}>{value}</p>
      <p className={styles.metricSub}>{sub}</p>
    </div>
  )
}

function InfoChip({ label, value }) {
  return (
    <div className={styles.chip}>{label} <span>{value}</span></div>
  )
}

function EmptyState() {
  return (
    <div className={styles.card}>
      <div className={styles.emptyState}>
        <p className={styles.emptyText}>Configure parameters and calculate to see results</p>
      </div>
    </div>
  )
}
