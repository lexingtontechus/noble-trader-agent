'use client'
import styles from './FactorBar.module.css'

export default function FactorBar({ name, value, max, color }) {
  const pct = Math.min(100, (value / max) * 100)
  return (
    <div className={styles.row}>
      <span className={styles.name}>{name}</span>
      <div className={styles.barWrap}>
        <div className={styles.bar} style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className={styles.val}>{value.toFixed(3)}</span>
    </div>
  )
}
