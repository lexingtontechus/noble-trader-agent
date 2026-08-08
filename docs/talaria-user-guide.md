# Talaria — User Reference Guide

**Talaria** is your client-facing Noble Trader dashboard inside the Hermes
desktop app. It connects **directly to the Noble Trader data layer** (Supabase)
with your subscription — no local server, no agent runtime, no broker
connection. It shows the strategy's signals, renko price structure, analytics,
and the paper (simulated) portfolio.

> **Data freshness:** everything auto-refreshes every 60s; live signals and
> paper events also stream in realtime over the socket when available.
> **Subscription:** your plan unlocks the symbol set and the Pro-only sections.

---

## The top stat strip

| Card | What it tells you |
|---|---|
| **Plan** | Which subscription tier you're on: `Precision Pro` or `Signal Scout`, and its status (active / grace / expired). |
| **Symbols** | How many symbols your plan unlocks — filtered to symbols that actually have recent sweep data, so every listed symbol is live. |
| **Realtime** | Socket state: `Live` (streaming), `Connecting`, or `Poll fallback` (REST every 60s — still works, just slower). |
| **Hot signals** | How many **qualified** (tradeable) signals fired in the last 10 minutes, ranked by Kelly — the engine's current conviction. |

## Hot signals banner

Colored chips of the most recent qualified signals: **blue = buy, red = sell**,
each showing the effective Kelly fraction (e.g. `kelly 0.240` = the engine
would risk 24% of equity on that signal). Stale signals drop off after 10
minutes — this is the "what's actionable right now" view.

## Kelly by symbol (latest sweep)

A bar chart, one bar per active symbol, from the **latest sweep cycle**:
bar length = effective Kelly (position sizing fraction), blue = buy, red =
sell. It's a cross-symbol snapshot of where the engine currently has
conviction and how strong it is.

## Renko bricks — last 10 (per symbol)

The price-structure chart for **one selected symbol** (buttons above the
chart; the default is the first active symbol).

- Each **brick** = a fixed-price move; **up = blue**, **down = red**.
- Dashed **ENTRY / SL / TP** reference lines appear when they fall inside the
  visible range (from the latest sweep for that symbol).
- The **`levels:` legend below the chart** always shows the full ENTRY / SL /
  TP prices, so you never have to guess the numbers.
- The Y-axis is scaled to the bricks themselves (not the far-away levels), so
  wide-price symbols like BTCUSD render cleanly.
- Prices use **full value** (e.g. `$64900.00`, `$57.0568`) — no truncated
  digits.

## Markov + pattern

Analyzes **the symbol you selected in the chart above** (it says which symbol
in the hint). Two cards:

| Card | Meaning |
|---|---|
| **Brick pattern** | Classifies the direction sequence of the **last 10 bricks**: `3-push` (three consecutive same-direction bricks — a thrust), `pullback` (up-up-down — a retracement after an up-push), `chop` (strictly alternating — no trend), or `neutral`. Short-term shape only. |
| **Markov P(up in 3)** | A **3-state UP/DOWN/FLAT Markov chain** fitted on **up to 200 brick closes** of that symbol, reporting the probability the next 3-brick move is UP. `50%` = no edge; `>50%` leans bullish (green); `<50%` leans bearish (red). |

**Nuance:** the *pattern* is a short-term read (last 10 bricks) while the
*Markov* number is a longer statistical fit (up to 200 bricks). They measure
different windows on purpose — the pattern answers "what does the recent shape
look like?", the Markov answers "given the whole recent history, what's the
odds of the next move?"

## Paper portfolio — Precision Pro

The **simulated** (paper) trade book — the trades the engine *would* have
taken. **No real money.**

- **Paper equity** = cumulative realized PnL of the simulated portfolio over
  time (each day adds that day's closed-trade PnL). If it shows `$0.00` early
  on, that's because **PnL is only realized when a trade CLOSES** — open
  positions show `—` in the table until the backend closes them.
- **R-multiple** = PnL ÷ risk per trade. `R = 1` means you made exactly one
  unit of risk on that trade; `R = -1` means you lost one unit. It normalizes
  results across symbols with very different dollar sizes (BTC vs EURUSD).
- Paginated 8 rows per page; live events stream in as the backend opens and
  closes positions.

## Signal health scoreboard

Per-symbol performance over the **last 30 days** of resolved signals
(trades that hit TP, SL, or expired):

| Column | Meaning |
|---|---|
| **Resolved** | Number of closed signals for that symbol. |
| **Win rate** | % of resolved signals that hit TP. |
| **WR LB** | **Wilson lower bound** (95% confidence) — the *conservative* win rate after accounting for sample size. A high bound is more trustworthy than a high raw win rate on few trades. |
| **Bias** | Predicted win probability − realized win rate. See Calibration bias below. |
| **Profit factor** | Gross wins ÷ gross losses (above 1.0 = profitable). |
| **Total PnL** | Cumulative paper PnL from that symbol's resolved signals. |
| **sig badge** | The symbol **survives BH-FDR** — its edge is statistically significant after correcting for testing many symbols at once. |

## Calibration bias (7d)

The model's **honesty check**: when it says "62% win probability", does it
actually win 62% of the time?

- **OVERCONFIDENT** (red) = it **predicted a higher win rate than it
  delivered** — the model thinks it wins more than it does. Be cautious with
  this symbol's signals.
- **UNDERCONFIDENT** (green) = it wins **more** than predicted — predictions
  are too pessimistic, which is usually good for you.
- **Bias ≈ 0** = well calibrated — predictions match reality.

## Sizing what-if

Shows the position-sizing math for the latest signal (same arithmetic the
engine uses, displayed transparently):

- **Baseline size** = `equity × effective Kelly × regime multiplier`
  (regime multiplier: e.g. high-vol breakout = 1.5×, choppy range = 0.5×,
  risk-off = −1.0× — the engine sizes *down* or stays out in bad regimes).
- **Final size (capped)** = baseline clipped by current portfolio drawdown
  and capped at **5% of equity**.

## Portfolio stats — Precision Pro

Risk-adjusted tear-sheet of the paper book:

- **Sharpe** — reward per unit of volatility (higher = better).
- **Sortino** — like Sharpe but only counts downside moves.
- **Calmar** — annualized return ÷ max drawdown.
- **Max DD %** — worst peak-to-trough decline.
- **Vol ann %** — how jumpy daily returns are (annualized).
- **Profit factor** — gross wins ÷ gross losses (above 1.0 = profitable).
- **Total PnL** — cumulative paper profit, with trade count + win rate.

A dash (`—`) means there aren't enough **closed** trades yet for a meaningful
number — this is expected early on.

## Paper vs equal-weight — Precision Pro

**Is the strategy beating the benchmark?** Each day compares:
- **Paper PnL** — what the signal engine's Kelly/regime-sized trades earned.
- **Equal-wt PnL** — what you would have made betting the same amount on every
  symbol with no regime filter.

**Delta > $0 (green)** = the engine beat the naive equal-weight benchmark that
day. **Delta < $0 (red)** = the benchmark won. This is the "does the strategy
add value over just trading everything equally?" card.

---

## FAQ

**Q: Why is paper PnL blank / $0.00?**
PnL is realized only when the backend **closes** a position. Open positions
show `—`; early on there may be nothing closed yet, so equity sits at $0.00.
This is expected, not a bug.

**Q: What is R-multiple?**
PnL ÷ risk per trade, a symbol-size-neutral way to compare results
(`R = 1` = one unit of risk made, `R = -1` = one unit lost).

**Q: Is this real money?**
No. Everything under "Paper" is simulated. Talaria is an analytics/signal
product; it never places live orders.

**Q: How often does data update?**
60-second REST polling, plus realtime push for live signals and paper events
when the socket is connected.

**Q: Why does the Markov card change when I pick a different symbol?**
It analyzes whatever symbol is selected in the Renko chart above it — it's a
per-symbol read, not a portfolio-wide stat.
