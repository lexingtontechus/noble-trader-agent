import { useCallback } from 'react'

// ─── Utilities ────────────────────────────────────────────────────────────────

export function clamp(x, lo, hi) {
  return Math.max(lo, Math.min(hi, x))
}

function zeroResult() {
  return {
    risk_fraction: 0,
    risk_amount: 0,
    units: 0,
    contracts: 0,
    masaniello_factor: 0,
    quality_factor: 0,
    drawdown_factor: 0,
    volatility_factor: 0,
    expected_edge: 0,
  }
}

// ─── Dynamic Masaniello Sizer ─────────────────────────────────────────────────
//
// Mirrors DynamicMasanielloSizer from dataclass.md.
// f_i = β · M_i · Q_i · DD_i · V_i   (clamped to [min_risk, max_risk])
//
// M_i (masaniello_factor) = (W − w) / (N − i + 1)
// Q_i (quality_factor)    = prob_factor × regime_factor × confidence_factor
// DD_i (drawdown_factor)  = clamp(1 − dd/max_dd, 0.25, 1.0)
// V_i (volatility_factor) = clamp(atr_baseline / atr_current, 0.5, 1.5)

export function sizeTrade(params) {
  const {
    equity,
    stop_distance,
    point_value,
    p_win,
    reward_risk,
    regime_quality,
    state_confidence,
    current_drawdown,
    atr_baseline,
    atr_current,
    wins_so_far,
    trade_index,
    batch_size,
    target_wins,
    base_risk,
    min_risk,
    max_risk,
    max_drawdown,
    use_kelly,
    kelly_fraction,
    min_prob,
    regime_floor,
  } = params

  // ── Guard checks ──
  if (equity <= 0) {
    return { allowed: false, reason: 'Equity must be positive.', ...zeroResult() }
  }
  if (stop_distance <= 0 || point_value <= 0) {
    return {
      allowed: false,
      reason: 'Stop distance and point value must be positive.',
      ...zeroResult(),
    }
  }
  if (regime_quality < regime_floor) {
    return {
      allowed: false,
      reason: `Regime quality (${regime_quality.toFixed(2)}) below floor (${regime_floor.toFixed(2)}).`,
      ...zeroResult(),
    }
  }
  if (p_win < min_prob) {
    return {
      allowed: false,
      reason: `Win probability (${p_win.toFixed(2)}) below minimum (${min_prob.toFixed(2)}).`,
      ...zeroResult(),
    }
  }

  // ── Expected edge: E = p·R − (1−p) ──
  const edge = p_win * reward_risk - (1 - p_win)
  if (edge <= 0) {
    return {
      allowed: false,
      reason: `Expected edge is non-positive (${edge.toFixed(3)}).`,
      ...zeroResult(),
      expected_edge: edge,
    }
  }

  // ── M_i: Masaniello progress factor ──
  const trades_left = Math.max(1, batch_size - trade_index + 1)
  const wins_needed = Math.max(0, target_wins - wins_so_far)
  const masaniello_factor = clamp(wins_needed / trades_left, 0, 1.5)

  // ── Q_i: Quality factor ──
  const prob_edge = Math.max(0, p_win - min_prob)
  const prob_factor = clamp(prob_edge / 0.1, 0, 1.5)
  const regime_factor = clamp(regime_quality, 0, 1.5)
  const confidence_factor = clamp(state_confidence, 0, 1)
  const quality_factor = prob_factor * regime_factor * confidence_factor

  // ── DD_i: Drawdown factor ──
  const dd_ratio = current_drawdown / Math.max(max_drawdown, 1e-9)
  const drawdown_factor = clamp(1 - dd_ratio, 0.25, 1.0)

  // ── V_i: Volatility factor ──
  const vol_raw = atr_current > 0 ? atr_baseline / atr_current : 1
  const volatility_factor = clamp(vol_raw, 0.5, 1.5)

  // ── Combined risk fraction ──
  let risk_fraction =
    base_risk * (0.5 + masaniello_factor) * quality_factor * drawdown_factor * volatility_factor

  // ── Optional Kelly overlay ──
  if (use_kelly) {
    const b = reward_risk
    const q = 1 - p_win
    const kelly_raw = b > 0 ? (b * p_win - q) / b : 0
    const kelly_cap = Math.max(0, kelly_raw) * kelly_fraction
    risk_fraction = Math.min(risk_fraction, kelly_cap > 0 ? kelly_cap : min_risk)
  }

  risk_fraction = clamp(risk_fraction, min_risk, max_risk)

  // ── Position size ──
  const risk_amount = equity * risk_fraction
  const dollars_per_unit = stop_distance * point_value
  const units = risk_amount / dollars_per_unit
  const contracts = Math.floor(units)

  if (contracts < 1) {
    return {
      allowed: false,
      reason: 'Calculated size is smaller than 1 contract/unit.',
      risk_fraction,
      risk_amount,
      units,
      contracts: 0,
      masaniello_factor,
      quality_factor,
      drawdown_factor,
      volatility_factor,
      expected_edge: edge,
    }
  }

  return {
    allowed: true,
    reason: 'Trade allowed.',
    risk_fraction,
    risk_amount,
    units,
    contracts,
    masaniello_factor,
    quality_factor,
    drawdown_factor,
    volatility_factor,
    expected_edge: edge,
  }
}

// ─── Markov State Probability Estimator ──────────────────────────────────────
//
// Blends regime-prior state distribution with uniform prior
// weighted by state_confidence, matching the HMM uncertainty model.

const REGIME_PRIORS = {
  bull_trend: { bull: 0.68, bear: 0.08, range: 0.14, volatile: 0.10 },
  bear_trend: { bull: 0.08, bear: 0.66, range: 0.16, volatile: 0.10 },
  range:      { bull: 0.20, bear: 0.18, range: 0.52, volatile: 0.10 },
  volatile:   { bull: 0.18, bear: 0.18, range: 0.22, volatile: 0.42 },
}

export function computeMarkovProbs(regime_label, state_confidence) {
  const base = REGIME_PRIORS[regime_label] || REGIME_PRIORS.bull_trend
  const conf = clamp(state_confidence, 0, 1)
  const u = 0.25 // uniform prior
  return {
    bull:     base.bull     * conf + u * (1 - conf),
    bear:     base.bear     * conf + u * (1 - conf),
    range:    base.range    * conf + u * (1 - conf),
    volatile: base.volatile * conf + u * (1 - conf),
  }
}

// ─── p_win sweep for sensitivity chart ───────────────────────────────────────

export function computeRiskSweep(baseParams) {
  const points = []
  for (let p = 0.45; p <= 0.85; p += 0.02) {
    const result = sizeTrade({ ...baseParams, p_win: p })
    points.push({
      p_win: parseFloat(p.toFixed(2)),
      risk_pct: result.allowed ? parseFloat((result.risk_fraction * 100).toFixed(4)) : 0,
    })
  }
  return points
}

// ─── React hook ──────────────────────────────────────────────────────────────

export function useSizer() {
  const calculate = useCallback((params) => {
    return sizeTrade(params)
  }, [])

  const getMarkovProbs = useCallback((regime_label, state_confidence) => {
    return computeMarkovProbs(regime_label, state_confidence)
  }, [])

  const getRiskSweep = useCallback((params) => {
    return computeRiskSweep(params)
  }, [])

  return { calculate, getMarkovProbs, getRiskSweep }
}
