'use client'
import styles from './Badge.module.css'

export default function Badge({ variant = 'default', children }) {
  return <span className={`${styles.badge} ${styles[variant]}`}>{children}</span>
}
