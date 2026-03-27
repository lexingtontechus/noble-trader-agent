'use client'
import { useState, useCallback } from 'react'
import AppHeader from './AppHeader'
import TradeForm from './TradeForm'
import ResultsPanel from './ResultsPanel'
import MarkovStates from './MarkovStates'
import BatchProgressChart from './BatchProgressChart'
import RiskSweepChart from './RiskSweepChart'
import SessionLog from './SessionLog'
import { useSizer, computeMarkovProbs } from '../hooks/useSizer'
import { useSupabase } from '../hooks/useSupabase'
import { DEFAULT_PARAMS } from '../lib/constants'
import styles from './RiskManagerClient.module.css'

export default function RiskManagerClient() {
  const [params, setParams]          = useState(DEFAULT_PARAMS)
  const [result, setResult]          = useState(null)
  const [saveStatus, setSaveStatus]  = useState(null)
  const [refreshTrigger, setRefresh] = useState(0)

  const { calculate }       = useSizer()
  const { saveCalculation } = useSupabase()

  const buildEngineParams = useCallback((p) => ({
    ...p,
    current_drawdown: p.drawdown / 100,
    base_risk:        p.base_risk    / 100,
    min_risk:         p.min_risk     / 100,
    max_risk:         p.max_risk     / 100,
    max_drawdown:     p.max_drawdown / 100,
    min_prob:         0.5,
    regime_floor:     0.5,
  }), [])

  const handleChange = useCallback((key, value) => {
    setParams((prev) => ({ ...prev, [key]: value }))
  }, [])

  const handleCalculate = useCallback(async () => {
    const engineParams = buildEngineParams(params)
    const res          = calculate(engineParams)
    const markovProbs  = computeMarkovProbs(params.regime_label, params.state_confidence)

    setResult(res)
    setSaveStatus('saving')

    const { error } = await saveCalculation({ params: engineParams, result: res, markovProbs })

    if (error) {
      setSaveStatus('error')
      setTimeout(() => setSaveStatus(null), 4000)
    } else {
      setSaveStatus('saved')
      setTimeout(() => setSaveStatus(null), 2000)
      setRefresh((n) => n + 1)
    }
  }, [params, calculate, buildEngineParams, saveCalculation])

  return (
    <div className={styles.app}>
      <AppHeader />

      {saveStatus && (
        <div className={`${styles.toast} ${styles[`toast_${saveStatus}`]}`}>
          {saveStatus === 'saving' && 'Saving…'}
          {saveStatus === 'saved'  && '✓ Saved to Supabase'}
          {saveStatus === 'error'  && '✕ Save failed — check console'}
        </div>
      )}

      <div className={styles.mainGrid}>
        <TradeForm params={params} onChange={handleChange} onCalculate={handleCalculate} />

        <div className={styles.rightCol}>
          <ResultsPanel result={result} params={params} />
          <MarkovStates regimeLabel={params.regime_label} stateConfidence={params.state_confidence} />
          <div className={styles.chartsRow}>
            <BatchProgressChart params={params} />
            <RiskSweepChart params={buildEngineParams(params)} />
          </div>
          <SessionLog refreshTrigger={refreshTrigger} />
        </div>
      </div>
    </div>
  )
}
