import { auth } from '@clerk/nextjs/server'
import { redirect } from 'next/navigation'
import HistoryClient from '../../components/HistoryClient'

export const metadata = {
  title: 'Noble Trading — Trade History',
}

export default async function HistoryPage() {
  const { userId } = await auth()
  if (!userId) redirect('/sign-in')
  return <HistoryClient />
}
