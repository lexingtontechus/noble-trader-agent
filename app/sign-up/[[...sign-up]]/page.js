import { SignUp } from '@clerk/nextjs'
import styles from '../../../components/AuthScreen.module.css'

export default function SignUpPage() {
  return (
    <div className={styles.screen}>
      <div className={styles.backdrop} />
      <div className={styles.content}>
        <SignUp
          appearance={{
            variables: {
              colorPrimary: '#c8f542',
              colorBackground: '#13151a',
              colorText: '#eef0f4',
              colorTextSecondary: '#6b7280',
              colorInputBackground: '#1c1f27',
              colorInputText: '#eef0f4',
              borderRadius: '8px',
            },
            elements: {
              card: { background: '#13151a', border: '1px solid rgba(255,255,255,0.12)', boxShadow: 'none' },
              formButtonPrimary: { background: '#c8f542', color: '#0b0c0f', fontWeight: '700' },
              footerActionLink: { color: '#c8f542' },
            },
          }}
        />
      </div>
    </div>
  )
}
