'use client'
import { useMemo } from 'react'
import { computeMarkovProbs } from '../hooks/useSizer'
import { STATE_COLORS, STATE_LABELS } from '../lib/constants'
import Panel from './ui/Panel'
import styles from './MarkovStates.module.css'

const STATES = ['bull', 'bear', 'range', 'volatile']

export default function MarkovStates({ regimeLabel, stateConfidence }) {
  const probs = useMemo(
    () => computeMarkovProbs(regimeLabel, stateConfidence),
    [regimeLabel, stateConfidence]
  )
  const dominant = STATES.reduce((a, b) => (probs[a] > probs[b] ? a : b))

  return (
    <Panel title="Markov State Probabilities" badge="HMM">
      <div className={styles.grid}>
        {STATES.map((s) => {
          const isDominant = s === dominant
          const isHigh = probs[s] > 0.2
          return (
            <div
              key={s}
              className={`${styles.stateTile} ${isDominant ? styles.dominant : isHigh ? styles.high : styles.low}`}
              style={isDominant ? { borderColor: STATE_COLORS[s], background: `${STATE_COLORS[s]}10` } : {}}
            >
              <p className={styles.stateLabel}>{STATE_LABELS[s]}</p>
              <p className={styles.stateProb} style={{ color: STATE_COLORS[s] }}>
                {(probs[s] * 100).toFixed(1)}%
              </p>
              <div className={styles.barWrap}>
                <div className={styles.bar} style={{ width: `${probs[s] * 100}%`, background: STATE_COLORS[s] }} />
              </div>
              {isDominant && <span className={styles.dominantTag}>dominant</span>}
            </div>
          )
        })}
      </div>
      <div className={styles.footer}>
        <span className={styles.footerNote}>
          confidence {(stateConfidence * 100).toFixed(0)}% · blended with uniform prior
        </span>
      </div>
    </Panel>
  )
}
