'use client'
import styles from './Panel.module.css'

export default function Panel({ title, badge, children, className = '' }) {
  return (
    <div className={`${styles.panel} ${className}`}>
      {(title || badge) && (
        <div className={styles.header}>
          {title && <span className={styles.title}>{title}</span>}
          {badge && <span className={styles.badge}>{badge}</span>}
        </div>
      )}
      <div className={styles.body}>{children}</div>
    </div>
  )
}
