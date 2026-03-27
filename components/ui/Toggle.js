'use client'
import styles from './Toggle.module.css'

export default function Toggle({ label, checked, onChange }) {
  return (
    <div className={styles.row}>
      <span className={styles.label}>{label}</span>
      <label className={styles.toggle}>
        <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        <span className={styles.slider} />
      </label>
    </div>
  )
}
