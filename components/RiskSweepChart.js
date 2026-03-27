'use client'
import { useMemo } from 'react'
import { Line } from 'react-chartjs-2'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Filler,
  Tooltip,
} from 'chart.js'
import Panel from './ui/Panel'
import { computeRiskSweep } from '../hooks/useSizer'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Filler, Tooltip)

export default function RiskSweepChart({ params }) {
  const sweep = useMemo(() => computeRiskSweep(params), [params])

  const data = {
    labels: sweep.map((p) => p.p_win.toFixed(2)),
    datasets: [{
      label: 'Risk %',
      data: sweep.map((p) => p.risk_pct),
      borderColor: '#c8f542',
      backgroundColor: 'rgba(200,245,66,0.08)',
      borderWidth: 2,
      pointRadius: 0,
      fill: true,
      tension: 0.4,
    }],
  }

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: { callbacks: { label: (ctx) => `Risk: ${ctx.parsed.y.toFixed(3)}%` } },
    },
    scales: {
      x: { grid: { color: 'rgba(255,255,255,0.04)' }, ticks: { color: '#6b7280', font: { size: 9 }, maxTicksLimit: 6 } },
      y: { grid: { color: 'rgba(255,255,255,0.04)' }, ticks: { color: '#6b7280', font: { size: 9 }, callback: (v) => `${v}%` } },
    },
  }

  return (
    <Panel title="Risk Sensitivity" badge="P_WIN SWEEP">
      <div style={{ position: 'relative', height: '180px' }}>
        <Line data={data} options={options} />
      </div>
      <p style={{ marginTop: '10px', fontSize: '11px', color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>
        risk % across p_win 0.45 → 0.85 · all other params fixed
      </p>
    </Panel>
  )
}
