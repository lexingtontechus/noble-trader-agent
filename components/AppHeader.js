'use client'
import { UserButton, useUser } from '@clerk/nextjs'
import styles from './AppHeader.module.css'

export default function AppHeader() {
  const { user } = useUser()
  return (
    <header className={styles.header}>
      <div className={styles.left}>
        <div className={styles.logoMark}>
          <svg viewBox="0 0 20 20" fill="none">
            <path d="M3 14L7 9L11 11L17 5" stroke="#0b0c0f" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            <circle cx="17" cy="5" r="2" fill="#0b0c0f" />
          </svg>
        </div>
        <div>
          <h1 className={styles.title}>Noble Trading — Risk Manager</h1>
          <p className={styles.sub}>Dynamic Masaniello · Markov Regime Engine</p>
        </div>
      </div>
      <div className={styles.right}>
        {user && (
          <span className={styles.userName}>
            {user.firstName || user.emailAddresses[0]?.emailAddress}
          </span>
        )}
        <div className={styles.statusPill}>v2.1 · LIVE</div>
        <UserButton
          appearance={{
            elements: {
              avatarBox: { width: '32px', height: '32px' },
              userButtonPopoverCard: { background: 'var(--surface)', border: '1px solid var(--border-accent)', borderRadius: '10px' },
              userButtonPopoverActionButton: { color: 'var(--text)', fontFamily: 'var(--font-mono)', fontSize: '13px' },
              userButtonPopoverActionButton__signOut: { color: 'var(--red)' },
              userButtonPopoverFooter: { display: 'none' },
            },
          }}
        />
      </div>
    </header>
  )
}
