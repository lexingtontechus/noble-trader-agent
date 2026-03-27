'use client'
import { Bar } from 'react-chartjs-2'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  Tooltip,
} from 'chart.js'
import Panel from './ui/Panel'

ChartJS.register(CategoryScale, LinearScale, BarElement, Tooltip)

export default function BatchProgressChart({ params }) {
  const { batch_size, wins_so_far, losses_so_far, trade_index } = params

  const labels = [], winData = [], lossData = [], currentData = []
  for (let i = 1; i <= batch_size; i++) {
    labels.push(`T${i}`)
    winData.push(i <= wins_so_far ? 1 : 0)
    lossData.push(i > wins_so_far && i <= wins_so_far + losses_so_far ? 1 : 0)
    currentData.push(i === trade_index ? 1 : 0)
  }

  const data = {
    labels,
    datasets: [
      { label: 'Win',     data: winData,     backgroundColor: '#22c55e', borderRadius: 3, barThickness: 14 },
      { label: 'Loss',    data: lossData,    backgroundColor: '#ef4444', borderRadius: 3, barThickness: 14 },
      { label: 'Current', data: currentData, backgroundColor: '#c8f542', borderRadius: 3, barThickness: 14 },
    ],
  }

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: { callbacks: { label: (ctx) => ctx.dataset.label } },
    },
    scales: {
      x: { stacked: true, grid: { color: 'rgba(255,255,255,0.04)' }, ticks: { color: '#6b7280', font: { size: 9 }, maxRotation: 0 } },
      y: { stacked: true, display: false, max: 1.2 },
    },
  }

  return (
    <Panel title="Batch Progress" badge="MASANIELLO">
      <div style={{ position: 'relative', height: '180px' }}>
        <Bar data={data} options={options} />
      </div>
      <div style={{ display: 'flex', gap: '14px', marginTop: '10px' }}>
        {[['#22c55e', 'Win'], ['#ef4444', 'Loss'], ['#c8f542', 'Current']].map(([color, label]) => (
          <span key={label} style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '11px', color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>
            <span style={{ width: '10px', height: '10px', borderRadius: '2px', background: color, display: 'inline-block' }} />
            {label}
          </span>
        ))}
      </div>
    </Panel>
  )
}
