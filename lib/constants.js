export const DEFAULT_PARAMS = {
  // Account
  equity: 10000,
  drawdown: 2,            // displayed as %, converted to decimal in hook

  // Trade setup
  stop_distance: 25,
  point_value: 1,
  reward_risk: 1.5,
  direction: 'long',

  // Markov / HMM signal
  p_win: 0.63,
  regime_quality: 1.1,
  state_confidence: 0.78,
  regime_label: 'bull_trend',

  // Volatility
  atr_baseline: 20,
  atr_current: 22,

  // Masaniello batch
  batch_size: 10,
  target_wins: 6,
  trade_index: 4,
  wins_so_far: 2,
  losses_so_far: 1,

  // Risk config (stored as %)
  base_risk: 0.5,
  min_risk: 0.25,
  max_risk: 1.0,
  max_drawdown: 10,

  // Kelly
  use_kelly: false,
  kelly_fraction: 0.25,
}

export const REGIME_LABELS = [
  { value: 'bull_trend', label: 'Bull Trend' },
  { value: 'bear_trend', label: 'Bear Trend' },
  { value: 'range',      label: 'Range' },
  { value: 'volatile',   label: 'Volatile' },
]

export const STATE_COLORS = {
  bull:     '#22c55e',
  bear:     '#ef4444',
  range:    '#3b82f6',
  volatile: '#f59e0b',
}

export const STATE_LABELS = {
  bull:     'Bull Trend',
  bear:     'Bear Trend',
  range:    'Range',
  volatile: 'Volatile',
}
