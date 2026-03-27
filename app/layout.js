import { ClerkProvider } from '@clerk/nextjs'
import { IBM_Plex_Mono, Syne } from 'next/font/google'
import '../styles/globals.css'

const syne = Syne({
  subsets: ['latin'],
  weight: ['400', '500', '700'],
  variable: '--font-syne',
  display: 'swap',
})

const ibmPlexMono = IBM_Plex_Mono({
  subsets: ['latin'],
  weight: ['400', '500'],
  variable: '--font-ibm-mono',
  display: 'swap',
})

export const metadata = {
  title: 'Noble Trading — Risk Manager',
  description: 'Dynamic Masaniello + Markov Regime Engine',
}

export default function RootLayout({ children }) {
  return (
    <ClerkProvider>
      <html lang="en" className={`${syne.variable} ${ibmPlexMono.variable}`}>
        <body>{children}</body>
      </html>
    </ClerkProvider>
  )
}
