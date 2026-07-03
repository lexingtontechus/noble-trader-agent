# Hermes Agent Training Course: From Market Novice to Quant Hedge Fund Manager

> A structured, step-by-step curriculum designed to train the Hermes agent (or a human operator) from basic market understanding through algorithmic trading, portfolio management, and risk management — ultimately operating as a quant hedge fund manager.

---

## Course Overview

| Stage | Title | Lessons | Prerequisite |
|---|---|---|---|
| **1** | Foundations of Markets & Trading | 1–8 | None |
| **2** | Quantitative Analysis & Statistics | 9–16 | Stage 1 |
| **3** | Algorithmic Trading & Strategy Development | 17–26 | Stage 2 |
| **4** | Portfolio Management & Risk | 27–36 | Stage 3 |
| **5** | Advanced Quant Finance & Hedge Fund Operations | 37–46 | Stage 4 |

**Total:** 46 lessons across 5 stages. Each lesson includes objectives, content, references, and a practical exercise tied to the Hermes platform.

---

## Stage 1: Foundations of Markets & Trading

### Lesson 1: Market Structure & Participants

**Objectives:**
- Understand how financial markets are organized (exchanges, OTC, dark pools)
- Identify market participants (retail, institutional, market makers, HFT)
- Understand order types (market, limit, stop, post-only, iceberg)

**Content:**
- Primary vs secondary markets
- Exchange-based (NYSE, NASDAQ) vs OTC (forex, crypto DEXs)
- Order book mechanics: bids, asks, spread, depth
- Maker vs taker liquidity; maker rebates
- Alpaca's market structure (IEX feed, smart order routing)
- Hyperliquid's order book model (on-chain perp DEX)

**References:**
- *Trading and Exchanges: Market Microstructure for Practitioners* — Larry Harris (Oxford, 2003)
- *Market Microstructure Theory* — Maureen O'Hara (Blackwell, 1995)
- Alpaca API docs: https://docs.alpaca.markets/docs/order-types
- Hyperliquid docs: https://hyperliquid.gitbook.io/

**Hermes Exercise:**
- Run `platform stream --symbols AAPL` and observe live order book updates
- Identify maker vs taker fills in the dashboard `/orders` page
- Examine the spread_bps field in `price_monitor_events`

---

### Lesson 2: Asset Classes & Instruments

**Objectives:**
- Understand stocks, commodities, forex, crypto (spot + perps)
- Understand leverage, margin, and position sizing basics
- Distinguish spot vs derivatives (futures, perpetual swaps)

**Content:**
- Equities: common stock, ADRs, ETFs (Alpaca)
- Commodities: gold (GLD), oil, agricultural products
- Forex: major pairs, cross pairs (future venue)
- Crypto: spot trading vs perpetual swaps (Hyperliquid)
- Perpetual swaps: funding rates, basis, liquidation mechanics
- Leverage: how it amplifies gains/losses, margin requirements
- Position sizing: fixed fractional, Kelly criterion (preview)

**References:**
- *Options, Futures, and Other Derivatives* — John C. Hull (Pearson, 11th ed.)
- *Derivatives: Markets, Valuation, and Risk Management* — Robert E. Whaley
- Hyperliquid perpetuals docs: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/perpetuals

**Hermes Exercise:**
- Run `platform backfill-market --symbol BTC-PERP --venue hyperliquid --timeframe 1m --days-back 7`
- Examine the `funding_rate` field in `nt_regime_log_local` table
- Observe how funding rates change in DuckDB: `SELECT symbol, funding_rate, annualized_pct FROM nt_regime_log_local ORDER BY sweep_timestamp DESC LIMIT 20`

---

### Lesson 3: Price Discovery & Market Data

**Objectives:**
- Understand how prices are formed (order matching, auction, AMM)
- Read OHLCV bars and tick data
- Understand timeframes (1s, 1m, 5m, 1h, 1d) and their use cases

**Content:**
- Price discovery mechanisms: continuous double auction, call auction
- OHLCV bars: open, high, low, close, volume — what each tells you
- Tick data vs bar data: when to use each
- Multi-timeframe analysis: 1m for entry, 1h for trend, 1d for context
- Venue-native data: why each venue has its own prices (no yfinance)
- Renko bars: how they filter noise and show pure price movement

**References:**
- *Technical Analysis of the Financial Markets* — John Murphy (New York Institute of Finance)
- *Advances in Financial Machine Learning* — Marcos López de Prado (Wiley, 2018), Chapter 2
- Renko bar construction: https://www.investopedia.com/terms/r/renkochart.asp

**Hermes Exercise:**
- Run `platform monitor --symbols BTC-PERP` and watch the TickAggregator build bars at 6 timeframes
- Query DuckDB for renko bricks: `SELECT * FROM ... ORDER BY brick_number DESC LIMIT 20`
- Compare 1m bar close prices vs renko brick close prices

---

### Lesson 4: Trading Strategies Overview

**Objectives:**
- Understand the main strategy categories
- Know when each category works (regime-dependent)
- Map strategy types to Noble Trader's approach

**Content:**
- Trend-following: moving average crossover, breakout, momentum
- Mean-reversion: Bollinger bands, RSI extremes, pairs trading
- Arbitrage: statistical arbitrage, funding arbitrage (cash-and-carry)
- Market making: providing liquidity, capturing spread + rebates
- Event-driven: earnings, FOMC, CPI, liquidation cascades
- Regime-dependent strategy selection: trend-following in calm_trend, mean-reversion in choppy_range

**References:**
- *Following the Trend* — Michael Covel (Wiley, 2012)
- *Pairs Trading* — Ganapathy Vidyamurthy (Wiley, 2004)
- *Algorithmic Trading* — Ernest Chan (Wiley, 2013)
- Noble Trader README (upstream platform documentation)

**Hermes Exercise:**
- Review Noble Trader heartbeats in DuckDB: `SELECT signal, regime, regime_conf FROM signal_heartbeats ORDER BY ts_received DESC LIMIT 50`
- Identify which regime each signal appeared in
- Map the regime to the strategy category Noble Trader is using

---

### Lesson 5: Risk Management Basics

**Objectives:**
- Understand position sizing as the primary risk control
- Grasp stop-loss and take-profit mechanics
- Understand the concept of R-multiple (risk-reward ratio)

**Content:**
- Fixed fractional sizing: risk X% of equity per trade
- Stop-loss types: fixed, ATR-based, trailing, brick-boundary
- Take-profit types: fixed, trailing, scale-out
- R-multiple: expressing PnL as multiples of initial risk
- Expectancy: (win_rate × avg_win) - (loss_rate × avg_loss)
- The 1% rule: never risk more than 1% of equity on a single trade
- Drawdown: peak-to-trough decline, time-in-drawdown, recovery

**References:**
- *Trade Your Way to Financial Freedom* — Van K. Tharp (McGraw-Hill, 2nd ed.)
- *The Definitive Guide to Position Sizing* — Van K. Tharp (IITM, 2nd ed.)
- *Money Management Strategies for Futures Traders* — Nauzer Balsara (Wiley)

**Hermes Exercise:**
- Review closed trades: `SELECT trade_id, net_pnl, r_multiple, risk_amount FROM pnl_realized ORDER BY ts DESC LIMIT 20`
- Calculate expectancy from the data
- Check the Hermes decision tree thresholds: `platform agent` (shows SL=-1%, TP=+2.5%, early TP=+4.5%)
- Review circuit breaker events: `SELECT * FROM circuit_breaker_events ORDER BY ts DESC LIMIT 10`

---

### Lesson 6: Market Microstructure for Crypto

**Objectives:**
- Understand Hyperliquid's on-chain order book model
- Grasping funding rates, basis, and liquidation mechanics
- Understand MEV and frontrunning risks

**Content:**
- On-chain vs off-chain order books
- Hyperliquid's L1 execution model (sub-second finality)
- Funding rates: 8h funding, annualized basis, premium/discount
- Liquidation cascade: how forced liquidations amplify moves
- Order book imbalance: (bid_qty - ask_qty) / (bid_qty + ask_qty)
- Liquidation heatmap: clusters of liquidation levels
- Post-only orders: maker rebates, avoiding taker fees

**References:**
- Hyperliquid docs: https://hyperliquid.gitbook.io/
- *Cryptoassets* — Chris Burniske and Jack Tatar (McGraw-Hill)
- *The Bitcoin Standard* — Saifedean Ammous (Wiley)
- Order book imbalance research: https://arxiv.org/abs/2103.13629

**Hermes Exercise:**
- Run `platform monitor --symbols BTC-PERP` and watch the L2 order book
- Check `OrderBookL2.imbalance` property in the monitor events
- Review funding rate events: `SELECT * FROM price_monitor_events WHERE event_type = 'funding_spike' ORDER BY ts DESC LIMIT 10`
- Observe how `funding_stress` meta-regime state affects sizing (multiplier = 0.2×)

---

### Lesson 7: Renko Bars & Price Action

**Objectives:**
- Understand renko bar construction (brick size, direction, reversal)
- Read renko patterns (breakout, double-top, reversal, consolidation)
- Understand why Noble Trader uses renko bars instead of candlesticks

**Content:**
- Renko construction: new brick forms when price moves by brick_size
- Advantages: filters noise, removes time component, shows pure trend
- Brick patterns:
  - Breakout: 3+ consecutive same-direction bricks
  - Trend: 2+ consecutive + majority same direction
  - Reversal: direction change in last brick
  - Double top/bottom: two similar highs/lows with dip/bump between
  - Pullback: trend then reversal
  - Consolidation: alternating directions
- How brick_size is optimized: Noble Trader's weekly sweep finds optimal brick_size per symbol
- Hermes's role: uses NT's brick_size (trusted) and analyzes patterns for entry timing

**References:**
- *Renko Bar Chart* — Steve Nison (introduced renko to Western traders)
- Investopedia Renko: https://www.investopedia.com/terms/r/renkochart.asp
- Noble Trader's Renko HFT Pipeline (upstream docs)

**Hermes Exercise:**
- Review the `RenkoConstructor` in `src/hermes/signals/renko_engine.py`
- Run the Phase 3 tests: `pytest tests/test_phase3.py -k renko -v`
- Observe brick patterns in the dashboard `/signals` page (brick_pattern column)
- Query: `SELECT brick_pattern, entry_strategy, COUNT(*) FROM trade_signals_blended GROUP BY 1, 2`

---

### Lesson 8: The Noble Trader Platform

**Objectives:**
- Understand what Noble Trader does (strategy brain)
- Understand what Hermes does (entry/execution brain)
- Know the division of labor between the two systems

**Content:**
- Noble Trader owns:
  - Strategy direction (buy/sell/neutral)
  - Renko brick_size optimization (weekly full sweep + 5/15min light sweeps)
  - Per-asset 4×4 HMM regime detection (vol × trend = 16 cells)
  - EV Engine v4 (p_win blending via log-odds pooling)
  - Kelly + Dynamic Masaniello sizing
  - Signal generation (direction, entry, stop, TP)
- Hermes owns:
  - Entry timing (when within the signal window to pull the trigger)
  - Execution method (market / limit / TWAP / post-only / iceberg)
  - Portfolio-level risk overlay (7-state meta-regime)
  - Position management (trailing stops, scenario projections, decision tree)
  - Self-learning (optimization, shadow mode, hypothesis tracking)
- The heartbeat: NT's full signal payload published via Redis every 5-15 min

**References:**
- Noble Trader README (in upstream project)
- Noble Trader marketing overview (in upstream project)
- Hermes roadmap §2.0 (Division of Labor)
- Hermes agent onboarding guide §5 (Trading Loop)

**Hermes Exercise:**
- Read the full heartbeat schema: `docs/roadmap.md` §5.1
- Review a heartbeat in DuckDB: `SELECT * FROM signal_heartbeats ORDER BY ts_received DESC LIMIT 1`
- Map each field to its owner (NT vs Hermes)
- Run `platform ingest --dry-run` to see the NT Redis connection config

---

## Stage 2: Quantitative Analysis & Statistics

### Lesson 9: Probability & Statistics for Trading

**Objectives:**
- Apply probability distributions to returns
- Understand hypothesis testing in trading contexts
- Compute and interpret confidence intervals

**Content:**
- Normal distribution: mean, std, skew, kurtosis
- Fat tails: why financial returns are NOT normal (Black Swan events)
- Log returns vs simple returns
- Hypothesis testing: null hypothesis, p-values, significance level
- Confidence intervals: bootstrap vs parametric
- Brier score: measuring prediction calibration
- Log-odds pooling: combining multiple probability estimates

**References:**
- *Probability and Statistics for Finance* — Svetlozar Rachev et al. (Wiley)
- *The Black Swan* — Nassim Nicholas Taleb (Random House, 2nd ed.)
- *Advances in Financial Machine Learning* — López de Prado, Chapter 4
- Log-odds pooling: https://en.wikipedia.org/wiki/Opinion_pooling

**Hermes Exercise:**
- Review the EV Engine's log-odds pooling formula in `docs/roadmap.md` §2.2.4
- Run `platform rigor` and examine the bootstrap confidence intervals
- Check Brier score / calibration analysis in `nt_sweep_results_local.dq_anomalies`

---

### Lesson 10: Time Series Analysis

**Objectives:**
- Analyze financial time series for patterns
- Understand autocorrelation, stationarity, and cointegration
- Apply ARIMA, GARCH models conceptually

**Content:**
- Stationarity: why it matters (ADF test, KPSS test)
- Autocorrelation function (ACF) and partial ACF (PACF)
- ARIMA models: autoregressive, integrated, moving average
- GARCH models: volatility clustering
- Cointegration: pairs trading foundation (Engle-Granger test)
- Hurst exponent: mean-reverting (< 0.5) vs trending (> 0.5) vs random (0.5)
- Realized volatility: annualized from log returns

**References:**
- *Analysis of Financial Time Series* — Ruey Tsay (Wiley, 3rd ed.)
- *Time Series Analysis and Its Applications* — Robert Shumway & David Stoffer (Springer)
- Hurst exponent: https://en.wikipedia.org/wiki/Hurst_exponent

**Hermes Exercise:**
- Run the IndicatorEngine and check the Hurst exponent: query `price_monitor_events` for Hurst values
- Review the `get_hurst_exponent()` method in `src/hermes/monitor/indicators.py`
- Compare Hurst values across different regimes

---

### Lesson 11: Technical Indicators Deep Dive

**Objectives:**
- Master ATR, EMA, RSI, VWAP, and their real-time computation
- Understand when each indicator is useful vs misleading
- Build custom indicators using rolling windows

**Content:**
- ATR (Average True Range): volatility measure, used for stop placement and circuit breakers
  - True Range = max(H-L, |H-prev_C|, |L-prev_C|)
  - Hermes uses ATR for the volatility circuit breaker (atr_current / atr_baseline ratio)
- EMA (Exponential Moving Average): trend identification
  - EMA(20): short-term, EMA(50): medium, EMA(200): long-term
  - Multiplier = 2 / (period + 1)
- RSI (Relative Strength Index): overbought/oversold
  - RSI > 70: overbought, RSI < 30: oversold
  - Divergence: price makes new high but RSI doesn't
- VWAP (Volume Weighted Average Price): institutional benchmark
  - Deviation from VWAP in bps: positive = above VWAP (potentially overbought)
- Z-score: how unusual is the current return?
  - |z| > 2: unusual, |z| > 5: anomaly (triggers Hermes anomaly detector)

**References:**
- *Technical Analysis of the Financial Markets* — John Murphy
- *New Concepts in Technical Trading Systems* — J. Welles Wilder (original ATR/RSI)
- Investopedia ATR: https://www.investopedia.com/terms/a/atr.asp

**Hermes Exercise:**
- Review `src/hermes/monitor/indicators.py` — the `IndicatorEngine` class
- Run `pytest tests/test_phase2.py -k indicator -v` to see indicator tests
- Query DuckDB for indicator values in monitor events
- Check how ATR feeds into the volatility circuit breaker: `src/hermes/portfolio/circuit_breakers.py`

---

### Lesson 12: Hidden Markov Models

**Objectives:**
- Understand HMM architecture (states, transitions, emissions)
- Grasping how Noble Trader uses dual 4-state HMMs (vol × trend)
- Understand Hermes's 7-state meta-regime as a portfolio-level overlay

**Content:**
- HMM basics: hidden states, observable emissions, transition probabilities
- Gaussian HMM: emissions follow Gaussian distribution
- Forward-backward algorithm: computing state posteriors
- Viterbi algorithm: most likely state sequence
- Noble Trader's approach:
  - Volatility HMM: 4 states (low, med-low, med-high, high)
  - Trend HMM: 4 states (strong_bear, bear, bull, strong_bull)
  - Combined: 16-cell regime table with risk multipliers (0.10×–1.75×)
- Hermes's 7-state meta-regime:
  - Portfolio-level (not per-asset)
  - Rule-based waterfall (fast, interpretable)
  - States: calm_trend, choppy_range, high_vol_breakout, regime_transition, risk_off, funding_stress, liquidity_drained
  - Each state has a sizing multiplier (1.0× → 0.0×) and entry aggressiveness

**References:**
- *Hidden Markov Models for Time Series* — Walter Zucchini & Ian MacDonald (Chapman & Hall)
- *Machine Learning for Algorithmic Trading* — Stefan Jansen (Packt, 2nd ed.), Chapter 9
- hmmlearn docs: https://hmmlearn.readthedocs.io/
- Noble Trader's 4×4 regime table: upstream README

**Hermes Exercise:**
- Review `src/hermes/signals/meta_regime.py` — the 7-state classifier
- Run `pytest tests/test_phase3.py -k meta_regime -v`
- Query: `SELECT meta_regime, sizing_multiplier, COUNT(*) FROM trade_signals_blended GROUP BY 1, 2`
- Trace how `risk_off` (correlation > 0.75) is detected from `CrossPriceMonitor`

---

### Lesson 13: Kelly Criterion & Position Sizing

**Objectives:**
- Derive the Kelly Criterion from first principles
- Understand fractional Kelly and its risk-reduction benefit
- Connect Kelly to Hermes's "trust + overlay" sizing approach

**Content:**
- Kelly derivation: maximize E[log(wealth)] → f* = (p*b - q) / b
  - p = probability of winning
  - b = win/loss ratio (payoff)
  - q = 1 - p
- Full Kelly is too aggressive (100% on a sure thing) — use fractional Kelly
  - Quarter-Kelly (0.25×): common in practice
  - Noble Trader uses effective_kelly (capped)
- Dynamic Masaniello: f_i = β × (0.5 + M_i) × Q_i × DD_i × V_i
  - 5 factors: base risk, batch urgency, model quality, drawdown protection, volatility adjustment
- Hermes's approach (trust + overlay):
  - Baseline = equity × NT_effective_kelly × meta_regime_multiplier
  - Drawdown adjustment: clip(1 - dd/max_dd, 0.25, 1.0)
  - Risk caps: max_position_pct, max_notional, gross_exposure_headroom, risk_amount_cap

**References:**
- *The Kelly Capital Growth Investment Criterion* — Leonard MacLean et al. (World Scientific)
- *Fortune's Formula* — William Poundstone (Hill and Wang)
- Kelly's original paper: Kelly, J.L. (1956). "A New Interpretation of Information Rate." Bell System Technical Journal.
- López de Prado, Chapter 14: "Backtesting Risk Management"

**Hermes Exercise:**
- Review `src/hermes/signals/sizing.py` — the SizingEngine
- Run `pytest tests/test_phase3.py -k sizing -v`
- Compare: NT's effective_kelly vs Hermes's final_size_usd after overlay
- Query: `SELECT nt_effective_kelly, sizing_multiplier, final_size_usd FROM trade_signals_blended LIMIT 10`

---

### Lesson 14: Markov Chains & Regime Transitions

**Objectives:**
- Understand discrete-time Markov chains
- Build transition probability matrices
- Use Markov properties for regime prediction

**Content:**
- Markov property: next state depends only on current state
- Transition matrix: P(next_state | current_state)
- Stationary distribution: long-run state probabilities
- Noble Trader's Markov chain: UP / DOWN / FLAT states with transition probabilities
- Regime shift detection: when transition probability spikes
- Hermes's regime_transition state: triggered by upstream shift OR high posterior entropy

**References:**
- *Introduction to Probability Models* — Sheldon Ross (Academic Press, 12th ed.)
- *Markov Chains* — J.R. Norris (Cambridge University Press)
- Noble Trader's markov_p_up / markov_p_dn fields in heartbeat

**Hermes Exercise:**
- Review heartbeat Markov fields: `SELECT markov_current_state, markov_p_up, markov_p_dn FROM signal_heartbeats LIMIT 20`
- Observe regime shift events: `SELECT * FROM signal_heartbeats WHERE regime_shift = TRUE ORDER BY ts_received DESC`
- Check meta_regime_history: `SELECT prev_state, new_state, trigger FROM meta_regime_history ORDER BY ts DESC`

---

### Lesson 15: Correlation & Portfolio Theory

**Objectives:**
- Understand covariance, correlation, and diversification
- Grasp Modern Portfolio Theory (Markowitz)
- Apply correlation to Hermes's risk_off regime detection

**Content:**
- Covariance: how two assets move together
- Correlation coefficient: normalized covariance (-1 to +1)
- Diversification: combining uncorrelated assets reduces portfolio risk
- Markowitz mean-variance optimization: efficient frontier
- Correlation breakdown in crises: correlations → 1 during market crashes
- Hermes's cross-price monitor: rolling 1h correlation between all symbol pairs
- risk_off trigger: mean |ρ| > 0.75 across portfolio

**References:**
- *Portfolio Selection* — Harry Markowitz (Wiley, 2nd ed.)
- *Modern Portfolio Theory and Investment Analysis* — Edwin Elton et al. (Wiley)
- Correlation regime detection: López de Prado, Chapter 16

**Hermes Exercise:**
- Run `platform monitor --symbols BTC-PERP,ETH-PERP,AAPL` and check the correlation matrix
- Review `src/hermes/monitor/cross_price.py` — the CrossPriceMonitor
- Query: `SELECT * FROM price_monitor_events WHERE event_type = 'correlation_shift' ORDER BY ts DESC LIMIT 10`
- Observe how high correlation triggers `risk_off` meta-regime

---

### Lesson 16: Value at Risk (VaR) & Conditional VaR (CVaR)

**Objectives:**
- Compute VaR using historical and parametric methods
- Understand CVaR (Expected Shortfall) and why it's better
- Apply VaR in Hermes's risk gate

**Content:**
- VaR: maximum expected loss at a given confidence level over a time horizon
  - "With 99% confidence, we won't lose more than $X in 1 day"
- Historical VaR: empirical percentile of return distribution
  - More robust to fat tails
- Parametric VaR: assume normal distribution
  - VaR = mean + z_alpha × std
  - Faster but underestimates tail risk
- CVaR (Expected Shortfall): average loss given loss exceeds VaR
  - CVaR ≥ VaR always
  - Better measure because it captures tail severity
- Hermes's VaR usage:
  - Pre-trade: var_pre and var_post in RiskDecision
  - Risk circuit breaker: VaR breach triggers de-risk
  - Tear sheet: VaR 95% and CVaR 95% in daily metrics

**References:**
- *Quantitative Risk Management* — Alexander McNeil et al. (Princeton)
- *Value at Risk* — Philippe Jorion (McGraw-Hill, 3rd ed.)
- López de Prado, Chapter 5: "Risk-Based Portfolio Optimization"

**Hermes Exercise:**
- Review `src/hermes/portfolio/var_calculator.py` — VaRCalculator
- Run `pytest tests/test_phase4.py -k var -v`
- Check VaR in risk decisions: `SELECT var_pre, var_post FROM risk_decisions ORDER BY ts DESC LIMIT 10`
- Review VaR in tear sheet: `platform pnl` (look for VaR 95% line)

---

## Stage 3: Algorithmic Trading & Strategy Development

### Lesson 17: Backtesting Fundamentals

**Objectives:**
- Understand event-driven vs vectorized backtesting
- Avoid common backtesting pitfalls (look-ahead bias, survivorship bias)
- Use Hermes's backtest engine for heartbeat replay

**Content:**
- Event-driven backtesting: simulate each tick/bar in chronological order
  - More realistic but slower
  - Hermes uses this approach
- Vectorized backtesting: apply strategy to entire array at once
  - Faster but less realistic
  - Useful for parameter sweeps
- Common biases:
  - Look-ahead: using future data in decisions
  - Survivorship: only testing on stocks that survived
  - Overfitting: fitting parameters to historical noise
- Hermes's backtest engine:
  - Replays NT heartbeats from `signal_heartbeats` table
  - Through full L4→L5→L3 pipeline
  - Uses temp DuckDB for isolation
  - Generates tear sheet from results

**References:**
- *Advances in Financial Machine Learning* — López de Prado, Chapters 7-8, 11-12
- *Building Winning Algorithmic Trading Systems* — Kevin Davey (Wiley)
- Backtesting pitfalls: Bailey, D., & López de Prado, M. (2014). "The Deflated Sharpe Ratio."

**Hermes Exercise:**
- Run a backtest: `platform backtest --symbols BTC-PERP --days-back 30 --equity 100000`
- Review the result in DuckDB: `SELECT * FROM backtest_runs ORDER BY ts_started DESC LIMIT 1`
- Compare backtest equity curve to live equity curve in dashboard `/pnl`

---

### Lesson 18: Walk-Forward Optimization

**Objectives:**
- Understand walk-forward analysis (WFA)
- Implement purged k-fold cross-validation
- Evaluate strategy decay

**Content:**
- Walk-forward: train on in-sample (IS), test on out-of-sample (OOS)
  - Slide the window forward, repeat
  - Compare IS vs OOS performance
- Purging: gap between train and test to prevent information leakage
  - López de Prado's method: remove `gap` bars between train and test
- Strategy decay: (IS Sharpe - OOS Sharpe) / IS Sharpe
  - > 20% decay: strategy may be overfit
  - Hermes's threshold: OOS Sharpe must be ≥ 80% of IS Sharpe
- Hermes's implementation:
  - `walk_forward_split()`: generates purged train/test splits
  - `walk_forward_evaluate()`: computes IS/OOS Sharpe + decay

**References:**
- López de Prado, Chapter 12: "Backtesting through Cross-Validation"
- *The Evaluation and Optimization of Trading Strategies* — Robert Pardo (Wiley, 2nd ed.)
- Bailey & López de Prado (2014): "The Deflated Sharpe Ratio"

**Hermes Exercise:**
- Run rigor checks: `platform rigor --symbols BTC-PERP --days-back 90`
- Review the walk-forward results (train Sharpe vs test Sharpe vs decay)
- Examine: `src/hermes/backtest/statistics.py` — `walk_forward_evaluate()`
- Run `pytest tests/test_phase7.py -k walk_forward -v`

---

### Lesson 19: Monte Carlo & Bootstrap Methods

**Objectives:**
- Apply bootstrap resampling for Sharpe confidence intervals
- Understand the difference between permutation and bootstrap
- Use Monte Carlo for trade reshuffling

**Content:**
- Bootstrap: resample WITH replacement → creates different subsamples
  - Each sample has different mean/std → distribution of Sharpe estimates
  - Percentiles give confidence intervals
- Permutation: resample WITHOUT replacement → same distribution
  - Sharpe is order-invariant (mean/std don't change) → not useful for Sharpe
- Monte Carlo trade reshuffling:
  - Bootstrap resample returns n_iterations times
  - Report 5th, 25th, 50th, 75th, 95th percentile Sharpe
  - p-value: fraction of bootstrap Sharpes ≥ original
  - Pass if 5th percentile > 0
- Hermes's implementation:
  - `monte_carlo_reshuffle()`: 1000 bootstrap iterations
  - Part of the 6-check statistical rigor suite

**References:**
- *An Introduction to the Bootstrap* — Bradley Efron & Robert Tibshirani (Chapman & Hall)
- *Bootstrap Methods and Their Application* — A.C. Davison & D.V. Hinkley (Cambridge)
- López de Prado, Chapter 13: "Backtesting Risk Management"

**Hermes Exercise:**
- Run `pytest tests/test_phase7.py -k monte_carlo -v`
- Review Monte Carlo output in `platform rigor` results
- Examine: `src/hermes/backtest/statistics.py` — `monte_carlo_reshuffle()`

---

### Lesson 20: Deflated Sharpe Ratio

**Objectives:**
- Understand why multiple testing inflates Sharpe ratios
- Compute the Deflated Sharpe Ratio (DSR)
- Use DSR as a gate for strategy acceptance

**Content:**
- Multiple testing problem: if you test 100 strategies, the best one will have an inflated Sharpe just by chance
- Expected maximum Sharpe from N independent trials: E[max] ≈ √(2 × ln(N))
- DSR formula (Bailey & López de Prado):
  - Adjusts observed Sharpe for:
    1. Number of trials (multiple testing penalty)
    2. Non-normality (skewness, kurtosis)
    3. Sample length
  - DSR > 1.0: probably real alpha
  - DSR < 1.0: likely noise
- Hermes's usage:
  - Part of the 6-check rigor suite
  - Computed in `deflated_sharpe_ratio()` function
  - Required to pass for optimization trials to be accepted

**References:**
- Bailey, D., & López de Prado, M. (2014). "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality." Journal of Portfolio Management.
- López de Prado, Chapter 14-15
- White, H. (2000). "A Reality Check for Data Snooping." Econometrica.

**Hermes Exercise:**
- Run `platform rigor` and find the DSR line
- Examine: `src/hermes/backtest/statistics.py` — `deflated_sharpe_ratio()`
- Run `pytest tests/test_phase7.py -k deflated -v`
- Observe how DSR decreases with more n_trials (multiple testing penalty)

---

### Lesson 21: Slippage & Transaction Cost Modeling

**Objectives:**
- Model market impact (slippage) for order sizing
- Understand maker/taker fee structures
- Optimize execution method to minimize costs

**Content:**
- Square-root impact model: slip = k × σ × √(participation_rate)
  - k: venue-specific constant (~0.1)
  - σ: annualized volatility
  - participation_rate: order_size / ADV
- Market orders: always pay slippage + taker fee
- Limit orders: zero slippage (price is fixed) but may not fill
- Post-only: negative slippage (maker rebate) but risk of non-fill
- TWAP: split into N slices → less slippage per slice but longer execution time
- Iceberg: split into small children → hides true size from market
- Venue fees:
  - Alpaca: 0 bps maker / 1 bps taker
  - Hyperliquid: ~0.5 bps maker / ~2 bps taker
- Hermes's execution method optimizer:
  - Large size → TWAP (split to avoid impact)
  - liquidity_drained → iceberg (hide size)
  - wait_for_brick_close → post_only (maker rebate)

**References:**
- *Market Impact Models* — Robert Almgren (Courant Institute, NYU)
- *Optimal Trading Strategies* — Robert Kissell (Wiley)
- Almgren, R., & Chriss, N. (2001). "Optimal Execution of Portfolio Transactions." Journal of Risk.

**Hermes Exercise:**
- Review `src/hermes/execution/slippage.py` — SlippageModeler
- Run `pytest tests/test_phase5.py -k slippage -v`
- Query fills: `SELECT price, arrival_price, slippage_bps, is_maker FROM fills ORDER BY ts DESC LIMIT 20`
- Compare slippage across order types in the dashboard `/orders` page

---

### Lesson 22: Entry Timing Optimization

**Objectives:**
- Understand why entry timing matters (the core of Hermes's value-add)
- Master the entry timing strategies per meta-regime
- Measure entry alpha (improvement vs NT's suggested entry)

**Content:**
- Why entry timing matters:
  - NT says "buy at $64,441" but the market moves
  - Entering at market: pay slippage, may enter at $64,500
  - Waiting for brick close: enter at $64,440 (brick boundary), zero slippage
  - Waiting for pullback: enter at $64,200 (better price), but risk missing the move
- Entry strategies by meta-regime:
  - calm_trend → enter_now (aggressive, market order): trend is strong, don't wait
  - choppy_range → wait_for_brick_close (patient, limit at brick): range-bound, patience pays
  - high_vol_breakout → wait_for_pullback (cautious, limit at pullback): vol is high, wait for better price
  - regime_transition → wait_for_retest (defensive, limit at retest): uncertain, wait for confirmation
  - risk_off/funding_stress → block: don't trade
  - liquidity_drained → maker_only (post_only): avoid taker fees in thin markets
- Entry alpha: (NT_entry - actual_entry) / NT_entry × 10000 (in bps)
  - Positive = Hermes entered better than NT suggested
  - This is the primary metric Hermes optimizes

**References:**
- *Algorithmic Trading* — Ernest Chan, Chapter 5: "Statistical Arbitrage"
- *Trading and Exchanges* — Larry Harris, Chapter 15: "Order Submission Strategies"
- Hermes roadmap §2.2.3 (Renko Entry Timing Engine)

**Hermes Exercise:**
- Review `src/hermes/signals/entry_timing.py` — EntryTimingOptimizer
- Run `pytest tests/test_phase3.py -k entry_timing -v`
- Query entry alpha: `SELECT symbol, entry_strategy, expected_entry_alpha_bps FROM trade_signals_blended ORDER BY ts_emitted DESC LIMIT 20`
- Run optimization: `platform optimize --symbols BTC-PERP --days-back 30 --n-trials 50`

---

### Lesson 23: The Blended Entry Value (BEV) Algorithm

**Objectives:**
- Understand the full BEV combiner from heartbeat to order
- Trace the complete signal processing pipeline
- Identify which component influences each decision

**Content:**
- BEV = the complete L4 pipeline that transforms a NT heartbeat into a BlendedSignal
- Step-by-step:
  1. Receive heartbeat from L0 (internal Redis)
  2. Trust NT's direction, entry, stop, TP, brick_size, effective_kelly
  3. Classify 7-state meta-regime (portfolio-level overlay)
  4. Construct renko bars from venue ticks
  5. Analyze brick pattern (breakout, trend, reversal, etc.)
  6. Entry timing decision (based on meta-regime + pattern)
  7. Sizing: NT effective_kelly × meta-regime multiplier × drawdown adjustment × risk caps
  8. Execution method selection (based on size + regime)
  9. Output BlendedSignal with: direction, entry_strategy, execution_method, final_size, meta_regime, brick_pattern, entry_alpha
- The BlendedSignal then flows to L5 (risk gate) → L3 (execution)

**References:**
- Hermes roadmap §5.4 (Entry/Execution Decision Algorithm)
- Hermes agent onboarding guide §5 (Trading Loop, Steps 4-6)
- Hermes source: `src/hermes/signals/synthesizer.py`

**Hermes Exercise:**
- Read `src/hermes/signals/synthesizer.py` — trace the full pipeline
- Run `pytest tests/test_phase3.py -k synthesizer -v`
- Review a BlendedSignal: `SELECT * FROM trade_signals_blended ORDER BY ts_emitted DESC LIMIT 1`
- Map each field to its source component (NT vs Hermes)

---

### Lesson 24: Renko Bar Construction & Pattern Analysis

**Objectives:**
- Implement renko bar construction from tick data
- Classify brick patterns for entry timing
- Understand multi-brick jumps and brick boundary snapping

**Content:**
- Construction algorithm:
  1. Start with first tick price as brick open
  2. If price moves up by brick_size → close current brick as UP, start new
  3. If price moves down by brick_size → close as DOWN, start new
  4. Multi-brick jump: if price moves >2× brick_size, create multiple bricks
  5. Snap close price to brick boundary (not raw tick price)
- Pattern classification (priority waterfall):
  1. Breakout: 3+ consecutive same direction
  2. Trend: 2+ consecutive + majority same direction
  3. Reversal: direction changed in last brick
  4. Double top/bottom: two similar highs/lows with dip/bump
  5. Pullback: trend then reversal
  6. Consolidation: alternating directions
  7. Unknown: insufficient data
- Pattern → entry strategy mapping:
  - Breakout + trend in calm_trend → enter_now (confirmed)
  - Consolidation in choppy_range → wait_for_brick_close
  - Breakout in high_vol_breakout → wait_for_pullback (don't chase)

**References:**
- *Renko Bar Chart* — Steve Nison
- Hermes source: `src/hermes/signals/renko_engine.py`
- Hermes tests: `tests/test_phase3.py` (renko construction + pattern tests)

**Hermes Exercise:**
- Read `src/hermes/signals/renko_engine.py` — RenkoConstructor + BrickPatternAnalyzer
- Run `pytest tests/test_phase3.py -k renko -v`
- Feed synthetic ticks and observe brick formation
- Query pattern distribution: `SELECT brick_pattern, COUNT(*) FROM trade_signals_blended GROUP BY 1`

---

### Lesson 25: Smart Order Routing

**Objectives:**
- Understand order routing for different execution methods
- Optimize between speed, cost, and fill probability
- Implement TWAP and iceberg execution

**Content:**
- Market order: fastest, highest slippage, always fills
- Limit order: zero slippage, may not fill, GTC or IOC
- Post-only: maker rebate, rejected if would cross (avoids taker fee)
- TWAP (Time-Weighted Average Price):
  - Split order into N child orders
  - Execute at regular intervals
  - Reduces market impact by spreading over time
  - Risk: market moves during execution
- Iceberg:
  - Split into small child orders (10% each)
  - Hide true order size from market
  - Reduces information leakage
  - Risk: slower execution, partial fills
- Hermes's SmartOrderRouter:
  - Creates Order objects from RiskDecision + BlendedSignal
  - Routes to correct order type based on execution_method
  - TWAP/iceberg: parent order with algo flag, paper engine splits

**References:**
- *Optimal Trading Strategies* — Robert Kissell
- *Algorithmic Trading & DMA* — Barry Johnson (4MyelomaPress)
- Kissell, R. (2013). *The Science of Algorithmic Trading and Portfolio Management.*

**Hermes Exercise:**
- Read `src/hermes/execution/router.py` — SmartOrderRouter
- Read `src/hermes/execution/paper_engine.py` — _fill_twap(), _fill_iceberg()
- Run `pytest tests/test_phase5.py -k twap -v` and `pytest tests/test_phase5.py -k iceberg -v`
- Compare fills: `SELECT order_id, COUNT(*) FROM fills GROUP BY 1 HAVING COUNT(*) > 1` (multi-fill orders)

---

### Lesson 26: Paper Trading & Live Readiness

**Objectives:**
- Understand the paper trading engine and its limitations
- Know when to transition from paper to live
- Implement safety checks for live trading

**Content:**
- Paper trading engine:
  - Simulates fills with slippage model
  - Venue-specific fees
  - Order state machine (DRAFT → FILLED)
  - No real money at risk
- Limitations:
  - Slippage model is simplified (square-root, not actual L2 depth)
  - No partial fills from real order book
  - No latency simulation
  - No rejected orders from venue
- Pre-live checklist:
  - Paper trading for 30+ days with positive expectancy
  - All 6 statistical rigor checks pass
  - Shadow mode completed (7+ days)
  - Decision tree validated (all branches tested)
  - Kill switch tested manually
  - Alert channels tested (Discord/Telegram)
  - DR runbook reviewed
  - Autonomy tiers configured correctly
  - Position sizes start small (tier 1: $5k max)

**References:**
- Hermes agent onboarding guide §6 (EOD Analysis & Self-Learning)
- Hermes DR runbook (docs/dr_runbook.md)
- Hermes roadmap §3.5 (Autonomy Tiers)

**Hermes Exercise:**
- Run full paper pipeline for 1 hour: ingest + monitor + synthesize + risk + execute
- Run `platform pnl` to generate tear sheet
- Run `platform rigor` to check statistical significance
- Run `platform alert-test` to verify alerts work
- Review autonomy tiers: `platform config show | grep autonomy`

---

## Stage 4: Portfolio Management & Risk

### Lesson 27: Portfolio Construction Theory

**Objectives:**
- Understand mean-variance optimization (Markowitz)
- Grasp risk parity and Black-Litterman models
- Apply portfolio theory to Hermes's asset allocation

**Content:**
- Markowitz mean-variance:
  - Efficient frontier: max return for each level of risk
  - Weights: solve quadratic optimization
  - Limitation: extremely sensitive to input estimates
- Risk parity:
  - Equal risk contribution from each asset
  - Not equal weight — assets with lower vol get higher weight
  - More robust than mean-variance (doesn't depend on return estimates)
- Black-Litterman:
  - Start with market equilibrium weights
  - Add investor views (subjective)
  - Blend using Bayesian framework
- Hermes's current approach:
  - Target allocation: 50% equities, 15% crypto, 20% commodities, 15% forex
  - Configurable rebalancing (on drift, daily, weekly, monthly)
  - Start small (4 initial symbols), scale later
  - Per-asset sizing from meta-regime multiplier (not full optimization)

**References:**
- *Modern Portfolio Theory* — Markowitz
- *Risk Parity Fundamentals* — Sebastian Maesso (Palgrave Macmillan)
- *Black-Litterman Model* — https://en.wikipedia.org/wiki/Black–Litterman_model

**Hermes Exercise:**
- Review `config/default.yaml` — portfolio.target_allocation
- Check current allocation: `platform config show | grep target_allocation`
- Observe how meta-regime multipliers adjust effective allocation

---

### Lesson 28: Drawdown Management

**Objectives:**
- Understand drawdown mechanics and psychology
- Implement drawdown-based position sizing
- Use the Ulcer Index for risk monitoring

**Content:**
- Drawdown: peak-to-trough decline in equity
  - Current DD = (peak - current) / peak
  - Max DD = worst historical drawdown
  - Time-in-DD: how long underwater
- Drawdown-based sizing:
  - dd_factor = clip(1 - DD/max_DD, 0.25, 1.0)
  - Reduces size as DD deepens
  - Floor at 0.25 (don't go to zero unless risk_off)
- Ulcer Index:
  - UI = sqrt(mean(DD%²))
  - Penalizes both depth and duration of drawdowns
  - Lower is better
- Recovery:
  - Half-life: time to recover half the drawdown
  - High-water mark: equity level before DD started
- Hermes's approach:
  - Portfolio DD > 15% → circuit breaker trips (halt + hedge)
  - Per-asset DD > 8% → asset-level kill switch
  - Daily loss > 3% → halt new entries for the day

**References:**
- *Drawdowns and Recovery* — López de Prado, Chapter 13
- *The Ulcer Index* — Peter Martin and Byron McCann (1987)
- Tharp, V.K. *Definitive Guide to Position Sizing*

**Hermes Exercise:**
- Review `src/hermes/analytics/pnl_service.py` — DrawdownTracker
- Run `platform pnl` and check the drawdown section (max DD, current DD, ulcer index)
- Query: `SELECT peak_equity, drawdown_pct, drawdown_usd, time_in_dd_sec FROM account_snapshots ORDER BY ts DESC LIMIT 10`
- Review circuit breaker events: `SELECT * FROM circuit_breaker_events WHERE breaker_type = 'risk' ORDER BY ts DESC`

---

### Lesson 29: Circuit Breakers & Risk Gates

**Objectives:**
- Understand the 4-level volatility circuit breaker
- Master the portfolio-level risk circuit breaker
- Implement the 8-check risk gate

**Content:**
- Volatility circuit breaker (per-asset, pre-trade):
  - Level 1 (REDUCE_50): ATR ratio > 2.5 → reduce size by 50%
  - Level 2 (BLOCK_ENTRIES): ATR ratio > 2.5 + edge too small → block
  - Level 3 (TIGHTEN_STOPS): ATR ratio > 4.0 → tighten stops
  - Level 4 (LIQUIDATE): meta_regime = risk_off → liquidate
- Risk circuit breaker (portfolio-level, continuous):
  - Portfolio DD > 15% → halt + hedge
  - Daily loss > 3% → halt new entries
  - VaR breach > 5% of equity → de-risk
  - Margin proximity > 80% → emergency deleverage
- Risk gate (8 pre-trade checks on BlendedSignal):
  1. Kill switch not active
  2. Volatility CB < Level 2
  3. Risk CB not tripped
  4. Account allocation ≤ max_gross_exposure
  5. Risk fraction ≤ cap
  6. Risk amount ≤ cap
  7. Reward:risk ≥ min (1.5)
  8. Autonomy tier allows autonomous execution
- Key principle: soft limits (4-6) CAP size, they don't reject. Hard limits (1-3, 7-8) REJECT.

**References:**
- Hermes roadmap §4 (Circuit Breakers)
- *Risk Management and Financial Institutions* — John Hull (Wiley, 5th ed.)
- *The Risk of Trading* — Michael R. O'Malley (Wiley)

**Hermes Exercise:**
- Read `src/hermes/portfolio/circuit_breakers.py` — VolatilityCircuitBreaker, RiskCircuitBreaker, KillSwitch
- Read `src/hermes/portfolio/risk_gate.py` — RiskGate (8 checks)
- Run `pytest tests/test_phase4.py -k circuit -v`
- Review risk decisions: `SELECT approved, limits_hit, reason FROM risk_decisions ORDER BY ts DESC LIMIT 20`

---

### Lesson 30: Autonomy Tiers & Human-in-the-Loop

**Objectives:**
- Understand the 5-tier autonomy matrix
- Know when human approval is required
- Configure autonomy thresholds for different environments

**Content:**
- Tier 0 (autonomous): read-only actions (query, backtest, report)
- Tier 1 (autonomous): small trades ≤ $5k, ≤ 2% equity
  - Requires: 7 days shadow, 8 rigor checks pass
- Tier 2 (notify-only): config promotion
  - Human can veto within 24 hours
  - Auto-promotes if no veto
- Tier 3 (human approval): large trades > $25k or novel strategy
  - 4-hour timeout → skip if no response
  - Degrades from tier 1 outside active hours (except crypto 24/7)
- Tier 4 (hard block): structural changes
  - Changing HMM state count
  - Disabling circuit breakers
  - Increasing max_gross_exposure > 2.0
  - Promoting failed rigor config
- Active hours:
  - Default: 09:30–16:00 ET (US market hours)
  - Crypto 24/7: exempt from degradation
  - Configurable timezone

**References:**
- Hermes roadmap §3.5 (Autonomy Tiers)
- Hermes agent onboarding guide §7.5 (Config Tuning)
- *The Haystack Report* — Nigel Seaton (autonomous systems risk)

**Hermes Exercise:**
- Review `src/hermes/portfolio/autonomy_gate.py` — AutonomyGate
- Run `pytest tests/test_phase4.py -k autonomy -v`
- Check your autonomy config: `platform config show | grep autonomy`
- Verify: `SELECT autonomy_tier FROM risk_decisions ORDER BY ts DESC LIMIT 10`

---

### Lesson 31: The Hermes Agent Decision Tree

**Objectives:**
- Master the full decision tree for position management
- Understand why native TP is suspended when a signal is present
- Know the order of checks (SL → signal → fading → early profit → hold)

**Content:**
- Validated decision tree (3 bugs found + fixed in Phase 9):
  1. HARD SL: pnl ≤ -1% → close (ALWAYS fires, risk management override)
  2. Signal present?
     - YES → Agent manages (native 2.5% TP suspended):
       a. Same direction:
          - pnl > 0 + 2+ adverse bricks → TRAIL (fading detected, protect gains)
          - pnl ≥ 4.5% + not fading → EARLY PROFIT (lock in outsized gain)
          - no exit condition → HOLD (trend still confirmed)
       b. Opposite direction:
          - strong (conviction ≥ 0.7 + regime confirms) → FLIP (close + reverse)
          - not strong → HOLD with native stops (don't flip on weak signals)
     - NO → Native stops manage:
          - pnl ≥ 2.5% → CLOSE (native take-profit)
          - otherwise → HOLD with native SL/TP
- Key insight: when a same-direction signal is present, agent suspends native 2.5% TP and uses 4.5% — letting profits run further when trend is confirmed
- Order matters: fading check BEFORE early profit (if fading, trail rather than exit — trend might resume)

**References:**
- Hermes source: `src/hermes/agent/decision_tree.py`
- Hermes tests: `tests/test_phase9.py` (26 tests including 7 validation tests)
- Hermes onboarding guide §5.2 Step 7 (Agent manages position)

**Hermes Exercise:**
- Read `src/hermes/agent/decision_tree.py` — all 9 actions
- Run `pytest tests/test_phase9.py -v` (all 26 tests including validation)
- Run `platform agent` (shows decision tree diagram)
- Trace a position through the tree: what happens at +3% with a buy signal? (HOLD — agent manages, native TP suspended, below 4.5% early profit)

---

### Lesson 32: PnL Attribution

**Objectives:**
- Decompose PnL into directional, timing, sizing, and regime components
- Understand what each component tells you about performance
- Use attribution to identify improvement areas

**Content:**
- Directional PnL: (exit - entry) × qty
  - Pure price move contribution
- Timing PnL: (NT_entry - actual_entry) × qty
  - Hermes's entry timing value-add
  - Positive = Hermes entered better than NT suggested
- Sizing PnL: deviation from "standard" size
  - Currently simplified to 0 (no deviation from baseline)
- Regime PnL: PnL × (regime_multiplier - 1.0)
  - How much the regime overlay helped or hurt
  - E.g., risk_off regime has multiplier -1.0 → regime_pnl = -gross_pnl
- Using attribution:
  - Timing PnL consistently negative → review entry timing strategy
  - Regime PnL consistently negative → review meta-regime classifier
  - Directional PnL negative but timing positive → NT's direction is wrong but Hermes's timing is good

**References:**
- *Performance Attribution* — Bernd Fischer & Russ Wermers (Cambridge)
- *Portfolio Performance Evaluation* — Jack Francis & Donald Ibbotson (Wiley)
- Hermes source: `src/hermes/analytics/pnl_service.py` — PnLAttribution

**Hermes Exercise:**
- Query attribution: `SELECT symbol, direction_pnl, timing_pnl, sizing_pnl, regime_pnl FROM pnl_realized ORDER BY ts DESC LIMIT 20`
- Run `platform pnl` and check the tear sheet (by-regime breakdown)
- Identify which component drives your PnL: `SELECT AVG(direction_pnl), AVG(timing_pnl), AVG(regime_pnl) FROM pnl_realized`

---

### Lesson 33: Cross-Asset Correlation Monitoring

**Objectives:**
- Build a real-time correlation matrix
- Detect correlation regime shifts
- Use correlation for diversification and crisis detection

**Content:**
- Rolling correlation: compute Pearson correlation on rolling window of returns
  - Window: 60 ticks (short-term) vs 1440 ticks (baseline)
  - Correlation matrix: N×N for N symbols
- Correlation shift detection:
  - If |corr_short - corr_baseline| > 0.3 → shift event
  - Triggers `correlation_shift` price monitor event
- Crisis detection:
  - Mean |ρ| > 0.75 across portfolio → `risk_off` meta-regime
  - Correlations → 1 during crises (diversification breaks down)
- Using correlation for position sizing:
  - High correlation → reduce total exposure (can't diversify)
  - Low correlation → can increase exposure (diversification benefit)
- Hermes's implementation:
  - `CrossPriceMonitor`: tracks all symbol pairs
  - `get_correlation_matrix()`: returns N×N matrix
  - Feeds into meta-regime `risk_off` trigger

**References:**
- *Analysis of Financial Time Series* — Ruey Tsay, Chapter 8
- *Correlation and Dependence* — Dominique Guégan (Springer)
- López de Prado, Chapter 16: "Unsupervised Learning: Correlation Clustering"

**Hermes Exercise:**
- Run `platform monitor --symbols BTC-PERP,ETH-PERP,AAPL` with 3+ symbols
- Check the correlation matrix on dashboard `/monitor` page
- Query shift events: `SELECT * FROM price_monitor_events WHERE event_type = 'correlation_shift' ORDER BY ts DESC`
- Review `src/hermes/monitor/cross_price.py` — CrossPriceMonitor

---

### Lesson 34: Funding Rate Management (Crypto Perps)

**Objectives:**
- Understand perpetual swap funding mechanics
- Detect funding blowouts and their impact
- Manage funding PnL accrual

**Content:**
- Funding rate: periodic payment between longs and shorts
  - Positive funding: longs pay shorts (market is long-heavy)
  - Negative funding: shorts pay longs (market is short-heavy)
  - Hyperliquid: 8h funding interval
- Annualized funding: funding × 3 × 365 × 100%
  - E.g., 0.01% per 8h → ~11% annualized (normal)
  - 0.06% per 8h → ~66% annualized (blowout)
- Funding blowout triggers:
  - Annualized > 50% → `funding_stress` meta-regime
  - Action: close funding-negative positions, block new perp entries
- Funding PnL accrual:
  - Long position with positive funding: pays funding (reduces PnL)
  - Short position with positive funding: receives funding (increases PnL)
  - Hermes tracks this in `funding_pnl` field
- Cash-and-carry arbitrage:
  - Buy spot, short perp → collect positive funding
  - If annualized funding > borrowing cost → risk-free profit

**References:**
- *Crypto Derivatives* — https://www.coindesk.com/learn/crypto-derivatives-perpetual-swaps-explained
- Hyperliquid funding docs: https://hyperliquid.gitbook.io/
- *The Basics of Funding Rates* — Binance Academy

**Hermes Exercise:**
- Review `src/hermes/monitor/funding_watcher.py` — FundingWatcher
- Query funding rates: `SELECT symbol, funding_rate, annualized_pct FROM price_monitor_events WHERE event_type = 'funding_spike' ORDER BY ts DESC`
- Check funding PnL: `SELECT symbol, funding_pnl FROM pnl_realized ORDER BY ts DESC LIMIT 20`
- Observe `funding_stress` meta-regime: `SELECT COUNT(*) FROM trade_signals_blended WHERE meta_regime = 'funding_stress'`

---

### Lesson 35: Rebalancing & Drift Management

**Objectives:**
- Understand portfolio drift and rebalancing triggers
- Implement threshold-based rebalancing
- Manage transaction costs from rebalancing

**Content:**
- Portfolio drift: as assets perform differently, allocation shifts from target
  - E.g., crypto doubles → becomes 30% instead of 15%
  - Need to rebalance back to target
- Rebalancing triggers:
  - On drift: when any asset class drifts > 10% from target
  - Time-based: daily, weekly, monthly
  - Event-based: regime shift, large PnL swing
- Rebalancing methods:
  - Threshold: rebalance only when drift > threshold
  - Target weight: rebalance to exact target weights
  - Risk parity: rebalance to equal risk contribution
- Transaction costs:
  - Rebalancing incurs fees + slippage
  - Too frequent → costs eat into returns
  - Too infrequent → drift increases risk
- Hermes's current approach:
  - Config: `rebalance_threshold_drift_pct: 0.10` (10% drift)
  - Config: `rebalance_frequency: on_drift`
  - Config: `rebalance_method: threshold`
  - Implementation: deferred to Phase 11 (advanced)

**References:**
- *Rebalancing* — William Bernstein (Efficient Frontier)
- *The rebalancing bonus* — https://www.efficientfrontier.com/ef/996/rebal.htm
- *Optimal Rebalancing* — David Stein (Journal of Portfolio Management)

**Hermes Exercise:**
- Review `config/default.yaml` — portfolio.rebalance_* settings
- Check current allocation drift: compare actual vs target in dashboard `/portfolio`
- Query account snapshots for exposure: `SELECT long_exposure_usd, short_exposure_usd, gross_exposure_usd FROM account_snapshots ORDER BY ts DESC LIMIT 10`

---

### Lesson 36: Stress Testing & Scenario Analysis

**Objectives:**
- Design stress test scenarios for the portfolio
- Understand historical crisis patterns
- Use Hermes's replay engine for scenario testing

**Content:**
- Stress test types:
  - Historical: replay 2008 GFC, 2020 COVID, 2022 LUNA crash
  - Hypothetical: BTC -50% in 1 day, SPY gap down 10%, funding spikes to 500%
  - Monte Carlo: simulate 1000 random return paths
- Historical crisis patterns:
  - 2008 GFC: correlations → 1, all assets fell together
  - 2020 COVID: fastest bear market in history (34 days), V-shaped recovery
  - 2022 LUNA: crypto-specific, perp funding went extreme, liquidation cascade
  - 2024 yen carry unwind: forex volatility spike, carry trades unwound
- Scenario analysis with Hermes:
  - Use `platform replay` to replay specific time periods
  - Check which circuit breakers would trip
  - Verify drawdown stays within limits
  - Test kill switch activation

**References:**
- *Stress Testing* — https://www.imf.org/en/Publications/fandd/issues/2019/12/what-is-stress-testing-basics
- *The Failure of Risk Management* — Douglas Hubbard (Wiley)
- Hermes DR runbook: `docs/dr_runbook.md`

**Hermes Exercise:**
- Run replay during a volatile period: `platform replay --start 2026-06-30T14:00:00 --end 2026-06-30T16:00:00`
- Review circuit breaker events during that period
- Check if kill switch would have activated: `SELECT * FROM circuit_breaker_events WHERE ts >= '2026-06-30'`
- Review the DR runbook: `docs/dr_runbook.md`

---

## Stage 5: Advanced Quant Finance & Hedge Fund Operations

### Lesson 37: The Self-Learning Loop

**Objectives:**
- Understand the full self-learning cycle
- Generate hypotheses from regime performance
- Manage the hypothesis lifecycle (propose → backtest → shadow → live)

**Content:**
- Self-learning loop (runs on schedule):
  1. Observe: pull all signals, fills, PnL from DuckDB
  2. Attribute: decompose PnL by {strategy, regime, asset, venue}
  3. Hypothesize: generate improvement hypotheses
     - Low win-rate regime (< 40%, 3+ trades) → propose reducing sizing
     - High win-rate regime (> 65%, 3+ trades, positive PnL) → propose increasing sizing
  4. Backtest: run hypothesis through simulation engine
  5. Validate: 6 statistical rigor checks
  6. Shadow: paper-trade for 7 days at 10% size
  7. Promote: auto-promote if shadow Sharpe ≥ 80% of backtest Sharpe
- Hypothesis lifecycle:
  - proposed → backtested → shadow → live
  -                         ↘ rejected
  -                              ↗ retired
- Auto-rollback: if promoted config underperforms in live for 14 days → rollback

**References:**
- *Advances in Financial Machine Learning* — López de Prado, Chapter 11
- *Metalearning* — https://en.wikipedia.org/wiki/Meta_learning_(computer_science)
- Hermes source: `src/hermes/agent/learning.py` — SelfLearningLoop, HypothesisTracker

**Hermes Exercise:**
- Run EOD analysis: `platform agent --eod`
- List hypotheses: `platform agent --list-hypotheses`
- Query hypotheses: `SELECT hypothesis_id, status, confidence, hypothesis FROM hermes_hypotheses ORDER BY ts_created DESC`
- Review the hypothesis lifecycle in DuckDB

---

### Lesson 38: Bayesian Optimization for Trading Parameters

**Objectives:**
- Understand Bayesian optimization (Optuna TPESampler)
- Design parameter search spaces for entry/execution optimization
- Interpret optimization results

**Content:**
- Why Bayesian optimization:
  - Grid search: exhaustive but slow (exponential in dimensions)
  - Random search: faster but wasteful (doesn't learn from past trials)
  - Bayesian: learns from past trials → focuses on promising regions
- Optuna TPESampler (Tree-structured Parzen Estimator):
  - Builds two density estimates: good trials vs bad trials
  - Samples from "good" density → focuses on promising regions
  - Supports: categorical, int, float parameters
- Hermes's 17-parameter search space:
  - Entry strategies per meta-regime (categorical)
  - Brick confirmation count (int 1-5)
  - Pullback depth (float 0.25-1.0)
  - Execution method (categorical)
  - TWAP N bricks (int 1-10)
  - Iceberg child % (float 5-25)
  - Limit offset bps (float 0-20)
  - Trailing stop method (categorical)
  - ATR multiplier (float 1.0-5.0)
  - Exit strategy (categorical)
  - Sizing multipliers per regime (float)
- Objective: maximize Deflated Sharpe (or entry alpha if no Sharpe)
- Baseline: every trial compared against "blindly execute at market"

**References:**
- *Algorithms for Optimization* — Mykel Kochenderfer & Tim Wheeler (MIT Press)
- Optuna docs: https://optuna.readthedocs.io/
- Bergstra, J., et al. (2011). "Algorithms for Hyper-Parameter Optimization." NeurIPS.

**Hermes Exercise:**
- Run optimization: `platform optimize --symbols BTC-PERP --days-back 30 --n-trials 50`
- Review results: `SELECT * FROM simulation_runs WHERE mode = 'entry_timing_sweep' ORDER BY sharpe DESC LIMIT 10`
- Check which parameters were explored: `SELECT params FROM param_optimizations ORDER BY trial_num LIMIT 10`
- Review: `src/hermes/backtest/optimizer.py` — RenkoSimulationEngine

---

### Lesson 39: Shadow Mode & Config Promotion

**Objectives:**
- Understand shadow mode as the bridge between backtest and live
- Implement auto-promotion gates
- Manage config versioning and rollback

**Content:**
- Shadow mode:
  - Run new config in parallel with live trading
  - Paper-trade at 10% of live size cap
  - Track performance separately (shadow_run_id tag)
  - Duration: 7 days (configurable)
- Auto-promotion gate:
  - Shadow Sharpe ≥ 80% of backtest Sharpe (no decay)
  - No circuit breaker trips during shadow period
  - No drawdown > max_portfolio_drawdown_pct
  - Decision: "auto" (promote), "rejected" (poor performance), "pending" (need more time)
- Auto-rollback:
  - If promoted config underperforms baseline in live for 14 days
  - Rollback to previous config_hash
  - Log in config_history table
- Config versioning:
  - Every config change → new config_hash → written to config_history
  - Old configs archived (still queryable in DuckDB)
  - Hot-reload via Redis `config.update` channel

**References:**
- *Continuous Delivery* — Jez Humble & David Farley (Addison-Wesley)
- *Canary Releases* — https://martinfowler.com/bliki/CanaryRelease.html
- Hermes source: `src/hermes/backtest/optimizer.py` — run_shadow_mode(), check_promotion()

**Hermes Exercise:**
- Start shadow mode: `platform shadow --symbols BTC-PERP --duration-days 7`
- Review shadow runs: `SELECT * FROM simulation_runs WHERE mode = 'shadow' ORDER BY ts_started DESC`
- Check promotion decisions: `SELECT run_id, promotion_decision, shadow_sharpe FROM simulation_runs WHERE promoted_to_shadow = TRUE`
- Review config history: `SELECT config_hash, ts, source, rationale FROM config_history ORDER BY ts DESC LIMIT 10`

---

### Lesson 40: Counterfactual Analysis

**Objectives:**
- Understand counterfactual reasoning in trading
- Replay trades under alternative configurations
- Use counterfactuals to improve future decisions

**Content:**
- Counterfactual: "What would have happened if we had done X instead of Y?"
- Types of counterfactuals:
  - Entry timing: "What if we'd waited for the brick close instead of market order?"
  - Sizing: "What if we'd sized 2× larger?"
  - Exit: "What if we'd held longer instead of taking early profit?"
  - Execution: "What if we'd used post_only instead of market?"
- Hermes's counterfactual engine:
  - Takes a closed trade_id
  - Replays under alternative entry strategies (enter_now, wait_for_brick_close, wait_for_pullback)
  - Computes entry alpha for each alternative
  - Reports which strategy would have been better
- Using counterfactuals:
  - If wait_for_brick_close consistently beats enter_now → update config
  - If a specific regime benefits from a different strategy → hypothesis for optimizer
  - Feed counterfactual results into the self-learning loop

**References:**
- *Counterfactual Reasoning* — https://plato.stanford.edu/entries/causation-counterfactual/
- *Causal Inference in Statistics* — Judea Pearl (Wiley)
- López de Prado, Chapter 11: "Backtesting Risk Management"

**Hermes Exercise:**
- Run counterfactual on a trade: `platform counterfactual --trade-id <uuid>`
- Review: `src/hermes/backtest/optimizer.py` — run_counterfactual()
- Query trades for counterfactual analysis: `SELECT trade_id, symbol, net_pnl, entry_strategy FROM pnl_realized ORDER BY ts DESC LIMIT 5`

---

### Lesson 41: The Dead Man's Switch & System Resilience

**Objectives:**
- Understand the dead man's switch as a safety net
- Configure alerting channels (Discord, Telegram)
- Implement forensic replay for post-incident analysis

**Content:**
- Dead man's switch:
  - Background monitor checks heartbeat health every 5 seconds
  - If no heartbeat from any component within 60 seconds → activate
  - Activation: kill switch on → cancel all orders → optionally flatten
  - Auto-deactivates when heartbeat resumes
  - Callback: sends critical alert to Discord/Telegram
- Alerting system:
  - 4 severity levels: info (blue), warning (yellow), critical (red), emergency (red)
  - Discord: rich embeds with color-coded severity + data fields
  - Telegram: Markdown messages with severity icons
  - Graceful no-op when channels not configured
- Forensic replay:
  - Replays any historical session from DuckDB
  - Merges 8 event types chronologically: heartbeats, signals, decisions, orders, fills, monitor events, circuit breakers, snapshots
  - Used for: debugging, postmortems, compliance audit, "what happened at 3:42 PM?"
- Load testing:
  - Simulates high-frequency heartbeat ingestion
  - Reports actual vs target throughput
  - Target: 100k heartbeats/day sustained

**References:**
- *Site Reliability Engineering* — Google SRE team (O'Reilly)
- *The Phoenix Project* — Gene Kim et al. (IT Revolution Press)
- Hermes DR runbook: `docs/dr_runbook.md`
- Hermes source: `src/hermes/ops/dead_mans_switch.py`, `src/hermes/ops/alerting.py`, `src/hermes/ops/replay.py`

**Hermes Exercise:**
- Run `platform alert-test` to verify alert channels
- Run `platform load-test --duration-sec 5 --rate-per-sec 100` to test throughput
- Run `platform replay --start <yesterday> --end <today>` for forensic analysis
- Review: `src/hermes/ops/dead_mans_switch.py` — heartbeat monitoring

---

### Lesson 42: Multi-Venue Architecture

**Objectives:**
- Understand the venue adapter pattern
- Design for adding new venues (OANDA, IBKR)
- Manage venue-specific quirks (fees, leverage, order types)

**Content:**
- VenueAdapter abstract base class:
  - connect(), disconnect()
  - stream_ticks(), stream_order_book()
  - fetch_historical_bars(), get_current_price()
  - normalize_symbol()
  - Optional: stream_funding_rates(), stream_liquidations()
- Current adapters:
  - Alpaca: stocks + commodities, IEX WebSocket, REST bars, no forex
  - Hyperliquid: crypto perps + spot, WS trades + L2 book, REST funding
- Future adapters (commented in config):
  - OANDA: forex, REST API, no WebSocket
  - IBKR: multi-asset, complex API, TWS gateway
- Venue-specific handling:
  - Symbol normalization: "BTC-PERP" → "BTC" (Hyperliquid), "AAPL" → "AAPL" (Alpaca)
  - Fee structure: Alpaca (0/1 bps maker/taker) vs Hyperliquid (0.5/2 bps)
  - Leverage: Alpaca (4×) vs Hyperliquid (50×)
  - Data: venue-native only (no yfinance), fail-hard policy
- Adding a new venue:
  1. Implement VenueAdapter interface
  2. Add venue config to `config/default.yaml`
  3. Add credentials to `.env.example`
  4. No core code changes needed

**References:**
- *Design Patterns* — Gamma, Helm, Johnson, Vlissides (Addison-Wesley) — Strategy pattern
- Hermes source: `src/hermes/transport/adapters/base.py` — VenueAdapter ABC
- Alpaca API: https://docs.alpaca.markets/
- Hyperliquid API: https://hyperliquid.gitbook.io/

**Hermes Exercise:**
- Review `src/hermes/transport/adapters/base.py` — the abstract interface
- Compare adapters: `src/hermes/transport/adapters/alpaca_adapter.py` vs `hyperliquid_adapter.py`
- Run `pytest tests/test_phase2.py -k adapter -v`
- Design a new venue adapter (OANDA) on paper

---

### Lesson 43: Data Pipeline Architecture

**Objectives:**
- Understand the full data flow from venues to DuckDB
- Master Parquet partitioning for historical data
- Design efficient DuckDB queries for analytics

**Content:**
- Data flow:
  1. Venue WebSocket → venue adapter → Tick/OrderBookL2 objects
  2. Ticks → TickAggregator → Bars (6 timeframes)
  3. Bars → IndicatorEngine → ATR/EMA/RSI/etc.
  4. Ticks → Parquet writer (partitioned by venue/symbol/tf/date)
  5. Signals/decisions/orders/fills → DuckDB (real-time)
  6. DuckDB → dashboard (read-only queries)
- Parquet partitioning:
  - Bars: `data/parquet/bars/venue={venue}/symbol={symbol}/tf={tf}/date={date}/part-*.parquet`
  - Ticks: `data/parquet/ticks/venue={venue}/symbol={symbol}/date={date}/part-*.parquet`
  - Snappy compression, batched writes (1000 rows or 5s)
- DuckDB views:
  - `market_bars`: reads all Parquet bar files via `read_parquet()` with hive partitioning
  - `market_ticks`: reads all Parquet tick files
  - Enables SQL queries over Parquet without importing
- Cold storage:
  - DuckDB: 90 days hot (fast queries)
  - Parquet: 90+ days warm (queryable via DuckDB views)
  - Archive: monthly Parquet export for cold storage

**References:**
- *Designing Data-Intensive Applications* — Martin Kleppmann (O'Reilly)
- DuckDB docs: https://duckdb.org/docs/
- Apache Parquet: https://parquet.apache.org/
- Hermes source: `src/hermes/transport/parquet_writer.py`

**Hermes Exercise:**
- Review `src/hermes/transport/parquet_writer.py` — ParquetWriter
- Run `platform stream --symbols BTC-PERP` and check Parquet files in `data/parquet/`
- Query DuckDB views: `SELECT * FROM market_bars LIMIT 10` (if views created)
- Review DuckDB table sizes: `SELECT table_name, COUNT(*) FROM ... GROUP BY 1`

---

### Lesson 44: DuckDB Schema & Query Patterns

**Objectives:**
- Understand the 23-table DuckDB schema (8 migrations)
- Write efficient analytical queries
- Use DuckDB for Hermes's self-learning analytics

**Content:**
- Schema overview (8 migrations, 23 tables):
  - v1: config_history, signal_heartbeats, account_snapshots, trade_journal, risk_decisions, circuit_breaker_events, hermes_hypotheses, meta_regime_history, audit_log, signal_heartbeats_quarantine, schema_version
  - v2: nt_sweep_results_local, nt_regime_log_local (NT mirrors)
  - v3: price_monitor_events
  - v4: trade_signals_blended
  - v5: orders, order_events, fills
  - v6: pnl_realized, pnl_unrealized
  - v7: backtest_runs
  - v8: simulation_runs, simulation_trades, param_optimizations
- Key query patterns Hermes uses:
  - Sharpe by regime: `SELECT strategy_id, regime_at_close, AVG(r_multiple) FROM pnl_realized GROUP BY 1, 2`
  - Worst 10 trades: `SELECT t.symbol, t.entry_thesis, p.net_pnl FROM trade_journal t JOIN pnl_realized p ON t.trade_id = p.trade_id ORDER BY p.net_pnl ASC LIMIT 10`
  - Slippage by venue: `SELECT venue, AVG(slippage_bps) FROM fills GROUP BY venue`
  - Hypothesis win rate: `SELECT h.hypothesis, h.status, AVG(p.r_multiple) FROM hermes_hypotheses h JOIN trade_journal t ON h.hypothesis_id = ANY(t.hypothesis_ids) JOIN pnl_realized p ON t.trade_id = p.trade_id GROUP BY 1, 2`
- DuckDB performance tips:
  - Use `read_only=True` for analytical queries (safe alongside writer)
  - DuckDB doesn't support partial indexes (WHERE on CREATE INDEX)
  - DuckDB doesn't support INSERT OR REPLACE (use delete + insert)
  - TEXT[] columns can't be indexed

**References:**
- DuckDB SQL docs: https://duckdb.org/docs/sql/
- Hermes schema: `src/hermes/db/schema.sql` + `src/hermes/db/migrations/`
- Hermes roadmap §6 (Local Analytical Storage — DuckDB)

**Hermes Exercise:**
- Review the full schema: `cat src/hermes/db/schema.sql src/hermes/db/migrations/*.sql`
- Run analytical queries from the Hermes roadmap §6.4
- Check table sizes: `python scripts/init_duckdb.py` (shows all tables + row counts)
- Write a custom query: find the best-performing meta-regime

---

### Lesson 45: Compliance & Audit

**Objectives:**
- Understand audit requirements for trading systems
- Use Hermes's audit trail for compliance
- Implement pre-trade compliance checks

**Content:**
- Audit trail (DuckDB tables):
  - `audit_log`: every action (actor, action, target, result, error, IP)
  - `config_history`: every config version (hash, source, rationale)
  - `circuit_breaker_events`: every CB trip (type, level, trigger, action)
  - `risk_decisions`: every pre-trade decision (approved, limits_hit, reason)
  - `signal_heartbeats`: every NT signal received (immutable)
  - `signal_heartbeats_quarantine`: malformed payloads (forensic review)
- Pre-trade compliance:
  - Restriction list: blocked symbols (sanctions, insider, halt)
  - Sector caps: max 40% in any sector
  - Wash-trade detection: prevent buying and selling same asset across venues
  - Best execution attestation: log venue, price, slippage vs NBBO
- Trade blotter export:
  - `SELECT * FROM fills ORDER BY ts ASC` → CSV for accountant/auditor
  - `SELECT * FROM pnl_realized ORDER BY ts ASC` → realized PnL report
- Immutable journal:
  - Hash-chained event log (planned for Phase 11)
  - Every event: `event_hash = SHA256(prev_hash + event_payload)`

**References:**
- *Compliance Handbook* — Securities and Exchange Commission
- *Market Abuse Regulation* — EU MAR
- Hermes roadmap §11 (Features Missed — Compliance & Audit)
- Hermes source: `src/hermes/db/schema.sql` — audit_log table

**Hermes Exercise:**
- Query audit log: `SELECT * FROM audit_log ORDER BY ts DESC LIMIT 20`
- Export trade blotter: write a DuckDB query to CSV
- Review config history: `SELECT config_hash, ts, source, rationale FROM config_history ORDER BY ts DESC`
- Check for wash trades: `SELECT symbol, side, COUNT(*) FROM fills GROUP BY 1, 2 HAVING COUNT(*) > 10`

---

### Lesson 46: Operating as a Quant Hedge Fund Manager

**Objectives:**
- Synthesize all skills into a daily operational rhythm
- Manage risk across the full portfolio
- Make data-driven decisions for continuous improvement

**Content:**
- Daily rhythm (the quant PM's day):
  1. **Pre-market** (08:30 ET): `platform health` → verify all systems
  2. **Market open** (09:30 ET): start pipeline (ingest, monitor, synthesize, risk, execute)
  3. **During market**: monitor dashboard, watch for circuit breaker alerts
  4. **EOD** (16:00 ET): `platform agent --eod` → postmortems + hypotheses
  5. **Post-market**: `platform pnl` → tear sheet, review performance
  6. **Weekly**: `platform optimize` → Bayesian sweep, `platform rigor` → statistical checks
  7. **Monthly**: retrain HMM, review hypotheses, rotate API keys, test DR
- Risk management philosophy:
  - Never risk more than 1% of equity per trade (risk_amount_cap)
  - Never let portfolio DD exceed 15% (circuit breaker)
  - Always pass 6 statistical rigor checks before promoting a config
  - Always shadow test for 7 days before going live
  - Trust NT's strategy, optimize Hermes's execution
- Continuous improvement:
  - Every closed trade → postmortem with lessons
  - Every EOD → hypotheses generated from regime performance
  - Every week → optimization sweep over 17 parameters
  - Every month → HMM retrain + hypothesis review
  - Every quarter → full strategy review + DR drill
- Key metrics to track:
  - Sharpe ratio (> 1.0 is good, > 2.0 is excellent)
  - Deflated Sharpe (> 1.0 suggests real alpha)
  - Max drawdown (< 15% is target)
  - Win rate (> 50% with R:R > 1.5)
  - Entry alpha (positive = Hermes adds value beyond NT)
  - Profit factor (> 1.5 is good, > 2.0 is excellent)
  - Ulcer index (lower is better)

**References:**
- *Inside the House of Money* — Steven Drobny (Wiley)
- *More Money Than God* — Sebastian Mallaby (Penguin)
- *The Man Who Solved the Market* — Gregory Zuckerman (Portfolio)
- Hermes agent onboarding guide (complete operational reference)
- Hermes DR runbook (emergency procedures)

**Hermes Exercise:**
- Complete a full day of paper trading using all 6 terminals
- Run `platform agent --eod` and review the analysis
- Run `platform pnl` and interpret every metric in the tear sheet
- Run `platform rigor` and check if your strategy passes all 6 checks
- Review all hypotheses: `platform agent --list-hypotheses`
- Write a daily journal entry summarizing what you learned

---

## Appendix A: Recommended Reading List

### Foundational
1. *Trading and Exchanges* — Larry Harris (market microstructure)
2. *Options, Futures, and Other Derivatives* — John Hull (derivatives)
3. *Technical Analysis of the Financial Markets* — John Murphy (indicators)
4. *Trade Your Way to Financial Freedom* — Van Tharp (position sizing)

### Quantitative
5. *Advances in Financial Machine Learning* — Marcos López de Prado (the bible)
6. *Analysis of Financial Time Series* — Ruey Tsay (time series)
7. *Probability and Statistics for Finance* — Rachev et al. (statistics)
8. *The Kelly Capital Growth Investment Criterion* — MacLean et al. (Kelly)
9. *Machine Learning for Algorithmic Trading* — Stefan Jansen (ML)

### Portfolio & Risk
10. *Portfolio Selection* — Harry Markowitz (MPT)
11. *Quantitative Risk Management* — McNeil et al. (VaR/CVaR)
12. *Risk Management and Financial Institutions* — John Hull (risk)
13. *The Failure of Risk Management* — Douglas Hubbard (risk failings)

### Hedge Fund
14. *Inside the House of Money* — Steven Drobny (hedge fund interviews)
15. *More Money Than God* — Sebastian Mallaby (hedge fund history)
16. *The Man Who Solved the Market* — Gregory Zuckerman (Renaissance Technologies)

### Systems & Engineering
17. *Designing Data-Intensive Applications* — Martin Kleppmann
18. *Site Reliability Engineering* — Google SRE team
19. *Continuous Delivery* — Jez Humble & David Farley

---

## Appendix B: Hermes Platform Reference

| Resource | Location | Content |
|---|---|---|
| Roadmap | `docs/roadmap.md` | 2,457-line system design (13 sections) |
| Onboarding | `docs/agent_onboarding.md` | 845-line operational guide |
| DR Runbook | `docs/dr_runbook.md` | 7 disaster recovery scenarios |
| Worklog | `worklog.md` | Development log by phase |
| Source code | `src/hermes/` | 48 Python files across 12 packages |
| Tests | `tests/` | 239 tests across 12 test files |
| Config | `config/default.yaml` | All configurable parameters |
| Secrets | `.env` (from `.env.example`) | All credentials (never in git) |
| DuckDB | `data/hermes.duckdb` | 23 tables, 8 migrations |
| Parquet | `data/parquet/` | Partitioned market data |
| Dashboard | `http://127.0.0.1:8080` | 11 pages, 7 DaisyUI themes |

---

## Appendix C: Key Formulas Quick Reference

| Formula | Expression | Where used in Hermes |
|---|---|---|
| Kelly Criterion | f* = (p×b - q) / b | SizingEngine (trusts NT's effective_kelly) |
| Masaniello | f_i = β × (0.5 + M_i) × Q_i × DD_i × V_i | NT owns this; Hermes trusts output |
| Log-odds pooling | P = inv_logit(Σ w_j × logit(p_j)) | L4 P_win computation |
| Sharpe ratio | S = (mean/std) × √252 | TearSheet, rigor checks |
| Sortino ratio | S_downside = mean / downside_std × √252 | TearSheet |
| Calmar ratio | C = annual_return / max_DD | TearSheet |
| Omega ratio | Ω = Σ(gains) / |Σ(losses)| | TearSheet |
| VaR (historical) | percentile(returns, 1-α) | VaRCalculator |
| CVaR | mean(returns[returns ≤ VaR]) | VaRCalculator |
| Square-root slippage | slip = k × σ × √(part_rate) | SlippageModeler |
| ATR | TR = max(H-L, |H-prev_C|, |L-prev_C|) → SMA | IndicatorEngine |
| EMA | EMA = (close - prev_EMA) × (2/(n+1)) + prev_EMA | IndicatorEngine |
| RSI | 100 - (100 / (1 + RS)), RS = avg_gain / avg_loss | IndicatorEngine |
| Deflated Sharpe | DSR = [SR×√(N-1) - Z_α×√(1-skew×SR+(kurt-1)/4×SR²)] / √(N-1+skew×SR+(kurt-1)/4×SR²) | statistics.py |
| Hurst exponent | H < 0.5: mean-reverting, H > 0.5: trending | IndicatorEngine |
| Ulcer index | UI = √(mean(DD%²)) | DrawdownTracker |

---

*This training course is designed to be completed at a pace of 2-3 lessons per day, taking approximately 3 weeks to complete all 46 lessons. Each lesson builds on the previous, so sequential completion is recommended.*
