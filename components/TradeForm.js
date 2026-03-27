'use client'
import Panel from './ui/Panel'
import { FieldInput, FieldSelect } from './ui/Field'
import Toggle from './ui/Toggle'
import { REGIME_LABELS } from '../lib/constants'
import styles from './TradeForm.module.css'

export default function TradeForm({ params, onChange, onCalculate }) {
  const set = (key) => (e) => {
    const raw = e.target.type === 'checkbox' ? e.target.checked : e.target.value
    const numeric = [
      'equity', 'drawdown', 'stop_distance', 'point_value', 'reward_risk',
      'p_win', 'regime_quality', 'state_confidence',
      'atr_baseline', 'atr_current',
      'batch_size', 'target_wins', 'trade_index', 'wins_so_far', 'losses_so_far',
      'base_risk', 'min_risk', 'max_risk', 'max_drawdown', 'kelly_fraction',
    ]
    const intFields = ['batch_size', 'target_wins', 'trade_index', 'wins_so_far', 'losses_so_far']
    let value = raw
    if (numeric.includes(key) && e.target.type !== 'checkbox') {
      value = intFields.includes(key) ? parseInt(raw, 10) || 0 : parseFloat(raw) || 0
    }
    onChange(key, value)
  }

  return (
    <Panel title="Trade Parameters" badge="INPUT">
      <p className={styles.sectionLabel}>Account</p>
      <div className={styles.row2}>
        <FieldInput label="Equity ($)" type="number" value={params.equity} onChange={set('equity')} min={1} />
        <FieldInput label="Current Drawdown (%)" type="number" value={params.drawdown} onChange={set('drawdown')} min={0} max={100} step={0.1} />
      </div>

      <p className={styles.sectionLabel}>Trade Setup</p>
      <div className={styles.row2}>
        <FieldInput label="Stop Distance ($)" type="number" value={params.stop_distance} onChange={set('stop_distance')} min={0.01} step={0.01} />
        <FieldInput label="Point Value ($)" type="number" value={params.point_value} onChange={set('point_value')} min={0.01} step={0.01} />
      </div>
      <div className={styles.row2}>
        <FieldInput label="Reward / Risk Ratio" type="number" value={params.reward_risk} onChange={set('reward_risk')} min={0.1} step={0.1} />
        <FieldSelect label="Direction" value={params.direction} onChange={set('direction')} options={[{ value: 'long', label: 'Long' }, { value: 'short', label: 'Short' }]} />
      </div>

      <p className={styles.sectionLabel}>Markov / HMM Signal</p>
      <div className={styles.row2}>
        <FieldInput label="Win Probability (p_win)" type="number" value={params.p_win} onChange={set('p_win')} min={0} max={1} step={0.01} />
        <FieldInput label="Regime Quality (0–1.5)" type="number" value={params.regime_quality} onChange={set('regime_quality')} min={0} max={1.5} step={0.05} />
      </div>
      <div className={styles.row2}>
        <FieldInput label="State Confidence (0–1)" type="number" value={params.state_confidence} onChange={set('state_confidence')} min={0} max={1} step={0.01} />
        <FieldSelect label="Regime Label" value={params.regime_label} onChange={set('regime_label')} options={REGIME_LABELS} />
      </div>

      <p className={styles.sectionLabel}>Volatility</p>
      <div className={styles.row2}>
        <FieldInput label="ATR Baseline" type="number" value={params.atr_baseline} onChange={set('atr_baseline')} min={0.01} step={0.1} />
        <FieldInput label="ATR Current" type="number" value={params.atr_current} onChange={set('atr_current')} min={0.01} step={0.1} />
      </div>

      <p className={styles.sectionLabel}>Masaniello Batch</p>
      <div className={styles.row2}>
        <FieldInput label="Batch Size (N)" type="number" value={params.batch_size} onChange={set('batch_size')} min={1} max={50} />
        <FieldInput label="Target Wins (W)" type="number" value={params.target_wins} onChange={set('target_wins')} min={1} max={50} />
      </div>
      <div className={styles.row2}>
        <FieldInput label="Trade Index (i)" type="number" value={params.trade_index} onChange={set('trade_index')} min={1} max={50} />
        <FieldInput label="Wins So Far (w)" type="number" value={params.wins_so_far} onChange={set('wins_so_far')} min={0} max={50} />
      </div>
      <div className={styles.row1}>
        <FieldInput label="Losses So Far (l)" type="number" value={params.losses_so_far} onChange={set('losses_so_far')} min={0} max={50} />
      </div>

      <p className={styles.sectionLabel}>Risk Config</p>
      <div className={styles.row2}>
        <FieldInput label="Base Risk (%)" type="number" value={params.base_risk} onChange={set('base_risk')} min={0.01} max={5} step={0.01} />
        <FieldInput label="Max Risk (%)" type="number" value={params.max_risk} onChange={set('max_risk')} min={0.01} max={5} step={0.01} />
      </div>
      <div className={styles.row2}>
        <FieldInput label="Min Risk (%)" type="number" value={params.min_risk} onChange={set('min_risk')} min={0.01} max={5} step={0.01} />
        <FieldInput label="Max Drawdown (%)" type="number" value={params.max_drawdown} onChange={set('max_drawdown')} min={1} max={50} step={0.1} />
      </div>

      <Toggle label="Kelly Overlay" checked={params.use_kelly} onChange={(val) => onChange('use_kelly', val)} />
      {params.use_kelly && (
        <div className={styles.row1}>
          <FieldInput label="Kelly Fraction" type="number" value={params.kelly_fraction} onChange={set('kelly_fraction')} min={0.05} max={1} step={0.05} />
        </div>
      )}

      <button className={styles.calcBtn} onClick={onCalculate}>
        Calculate Position Size
      </button>
    </Panel>
  )
}
