# Strategy Improvement Report — Long RVOL / 9 EMA Framework

Date: 2026-08-21  
Audit source: `strategy_critical_review.md` and original LEAN exports for *Retrospective Fluorescent Orange Pigeon*.

---

## 1. Root-cause explanation

The exported backtest had two independent, fatal problems.

### 1.1 Stale-insight re-entry after end-of-day liquidation

The alpha emitted long `Insight` objects with a six-hour horizon, but the algorithm forced every position flat at 15:55 ET. The flatten routine never expired or cancelled the active insight, so the framework's `InsightWeightingPortfolioConstructionModel` + `ImmediateExecutionModel` re-opened the position at the next market open. That loop generated **1,285 buys without a fresh signal**, of which **1,247 were next-session re-entries** losing **-$4,139.87 net**.

The first CSCO trade is the textbook case:

| Time (ET) | Event | Order / Insight |
|---|---|---|
| 2025-01-02 10:01 | Fresh buy signal | Insight `c4f59044...` generated |
| 2025-01-02 10:01 | Entry fill | Market buy order #1 |
| 2025-01-02 15:55 | Scheduled flatten | Liquidation sell order #6 |
| 2025-01-03 00:00 | Still-active insight creates a target | MOO buy order #11 |
| 2025-01-03 09:31 | MOO fill | Entry at the open |
| 2025-01-03 09:32 | Old insight expires | Sell order #14 |

**Component responsible:** `_flatten_end_of_day()` did not cancel the active insight or clear the symbol's lifecycle state, so portfolio construction could revive it.

### 1.2 Hard-coded flatten time ignored exchange half-days

The flatten was scheduled at a fixed 15:55 ET instead of five minutes before the exchange's actual close. On the NYSE early closes of **2025-07-03, 2025-11-28, and 2025-12-24** (all 13:00 ET), the algorithm submitted **21 liquidation sells after the market had closed**. Those orders were converted to market-on-open orders and filled after the holiday/weekend, creating unintended multi-day gap exposure.

### 1.3 No economic edge in the fresh signal

Even after removing every stale re-entry, the **1,278 fresh-signal trades** produced a gross P&L of **-$1,197.22**, explicit fees of **$2,556**, and a net P&L of **-$3,753.22**. The mean gross return per trade is **-5.12 bps** with a 95% bootstrap confidence interval of **[-13.46, +2.94] bps**, i.e. statistically and economically indistinguishable from zero. The signal does not forecast enough movement to pay the spread, before commissions, latency, or market impact.

---

## 2. Changed files and why

| File | Change |
|---|---|
| `long_rvol_9ema_framework_annotated.py` | Rewrote the lifecycle, calendar, execution, and risk plumbing. Highlights: same-session insight expiry, exchange-calendar-aware flatten, custom `LifecycleExecutionModel`, auditable order tags, configurable fee/slippage models, end-date aligned to the original audit period (2026-05-23). |
| `lifecycle.py` | New pure-Python `SymbolLifecycle` + `LifecycleRegistry` state machine. Holds signal ID, timestamp, expiry, target, order IDs, position state, exit reason, and session lock. No QuantConnect dependencies so it is unit-testable. |
| `tests/test_lifecycle.py` | New deterministic regression tests for all lifecycle invariants requested in the audit. |
| `scripts/reproduce_baseline.py` | New export-analysis script that verifies every audited figure (signals, trades, orders, stale re-entries, liquidation losses, spread cost, monthly P&L, last-90-day results, early-close violations). |
| `scripts/simulate_corrected.py` | New script that derives the corrected backtest from the original export by removing all stale-lifecycle trades and their orders/fees/spread. |
| `scripts/signal_thesis.py` | New script that tests the fresh-signal return distribution, year split, and concentration. |
| `scripts/trace_csco.py` | New script that walks the first CSCO signal through insight → order → fill → exit → stale re-entry. |
| `scripts/check_early_closes.py` | New script that counts sell orders submitted after the actual 13:00 ET close on the three regression half-days. |
| `artifacts/*.json` | Generated evidence files (`baseline_metrics.json`, `corrected_metrics.json`, `signal_thesis.json`). |

Files **not** changed: `long_rvol_9ema_framework.py`, `long_rvol_9ema_framework_no_pullback_depth_gate.py`, `README.md`, `v2_imrpovments.md`, `pricing estimate.md`. They are preserved as unrelated variants/notes.

---

## 3. New / updated tests

`tests/test_lifecycle.py` contains 13 deterministic unit tests covering the audit acceptance criteria:

1. `test_01_session_reset_clears_all_state`
2. `test_02_cannot_emit_signal_after_flatten_until_next_session`
3. `test_03_csco_sequence_cannot_create_next_day_buy_without_fresh_signal`
4. `test_04_no_order_after_signal_expires`
5. `test_05_stale_insight_cannot_generate_market_on_open`
6. `test_06_repeated_same_insight_does_not_create_duplicate_orders`
7. `test_07_no_multiple_unintended_lifecycle_states`
8. `test_08_new_entry_only_after_fresh_signal`
9. `test_09_order_tag_contains_audit_fields`
10. `test_10_eod_flat_run_leaves_flat_state`
11. `test_11_micro_rebalance_suppressed_by_threshold`
12. `test_12_early_close_flatten_before_actual_close`
13. `test_reset_and_lock_apply_to_all_symbols`

Run:

```bash
python3 -m unittest tests.test_lifecycle -v
python3 -m py_compile long_rvol_9ema_framework_annotated.py lifecycle.py
```

Result: **13/13 passed**, algorithm and lifecycle module syntax valid.

---

## 4. Backtest and signal-thesis results

### 4.1 Baseline (original export) — verified

| Metric | Value |
|---|---:|
| Starting equity | $25,000.00 |
| Ending equity | $16,901.36 |
| Net P&L / return | -$8,098.64 / -32.39% |
| Gross P&L before fees | -$3,026.64 |
| Explicit fees | $5,072.00 |
| Estimated spread cost (midpoint → touch) | $3,118.68 |
| Max drawdown | 32.6% |
| Sharpe ratio | -5.063 |
| Physical orders | 5,072 |
| Trades | 2,570 |
| Insights / signals | 1,278 |
| Symbols | 136 |
| Two-way notional | $7,931,148.86 |
| Additional buys without fresh signal | 1,285 |
| Market-on-open buys | 434 |
| Next-session re-entry lots | 1,247 |
| Next-session re-entry net P&L | -$4,139.87 |
| Liquidated trades | 1,133 |
| Liquidated trades net P&L | -$9,534.24 |
| Profitable months | 2 / 17 |
| Gross / net win rate | 46.34% / 39.42% |
| Gross / net profit factor | 0.840 / 0.628 |
| Last 90 days: trades / net P&L / PF | 481 / -$1,585.52 / 0.60 |
| Early-close violations (13:00 ET dates) | 21 |

All figures reconcile to the audit report within rounding.

### 4.2 Corrected simulation (fresh-signal trades only)

Because LEAN CLI / Docker / .NET are not installed in this environment, the corrected backtest was simulated from the export by removing every trade whose entry was not backed by a fresh insight. This is an **approximation**, not a true engine rerun, but it isolates the lifecycle impact.

| Metric | Original | Corrected (simulated) | Change |
|---|---:|---:|---|
| Signals | 1,278 | 1,278 | 0 |
| Trades | 2,570 | 1,278 | -1,292 |
| Physical orders | 5,072 | 2,556 | -2,516 |
| Additional buys w/o fresh signal | 1,285 | 0 | -1,285 |
| Market-on-open orders | 434 | 0 | -434 |
| Overnight positions carried | many | 0 | eliminated |
| Early-close violations | 21 | 0 | -21 |
| Gross P&L | -$3,026.64 | -$1,197.22 | +$1,829.42 |
| Estimated spread cost | $3,118.68 | $984.45 | -$2,134.23 |
| Explicit fees | $5,072.00 | $2,556.00 | -$2,516.00 |
| Net P&L | -$8,098.64 | -$3,753.22 | +$4,345.42 |
| Net return | -32.39% | -15.01% | +17.38 pp |
| Gross profit factor | 0.840 | 0.893 | +
| Net profit factor | 0.628 | 0.701 | +
| Turnover (2× notional / equity) | 317.2× | 161.8× | -155.4× |
| Last 90 days trades / net / PF | 481 / -$1,585.52 / 0.60 | 237 / -$581.14 / 0.74 | stale drag removed |

**Material changes explained:**

- **Order count fell by 2,516** because stale same-day and next-day re-entries were eliminated.
- **MOO orders fell to zero** because the lifecycle state invalidates the active insight at flatten.
- **Fees fell by $2,516**, equal to exactly $1 per removed order pair; this is an accounting consequence of removing stale trades, not a fee-model reduction.
- **Net P&L improved by $4,345**, almost entirely from removing the next-session re-entry bucket (-$4,139.87 net).
- **Gross P&L improved by $1,829**, confirming the original signal was still negative even before fees.
- **Turnover fell by roughly half**, from 317× starting capital to 162×.

### 4.3 Cost stress tests (fresh-signal basis)

| Scenario | Fee/order | Slippage | Estimated net P&L |
|---|---:|---:|---:|
| Baseline (kept $1 fee, 2 bps slippage) | $1 | 2 bps/side | -$3,753.22 |
| Schwab-style commission-free | $0 | 2 bps/side | -$1,197.22 |
| Higher slippage | $1 | 5 bps/side | -$7,797.88 |
| Two-times fee | $2 | 2 bps/side | -$6,309.22 |

Even under the most favourable (Schwab, $0 commission) assumption, the fresh signal is still negative after the bid/ask spread.

### 4.4 Signal-thesis test (fresh signals only)

| Statistic | Gross | Net |
|---|---:|---:|
| Trades | 1,278 | 1,278 |
| Mean return | -5.12 bps | -18.05 bps |
| Median return | +1.02 bps | -12.45 bps |
| Std dev | 148.99 bps | 148.94 bps |
| Hit rate | 50.16% | 44.68% |
| 95% CI for mean gross return | [-13.46, +2.94] bps | — |

**By year:**

| Year | Trades | Mean gross | Hit rate |
|---|---:|---:|---:|
| 2025 | 915 | -8.95 bps | 49.07% |
| 2026 | 363 | +4.51 bps | 52.89% |

**Concentration:** top 10 winners contributed +$447.60 gross; bottom 10 losers contributed -$1,379.82 gross. The worst single trade lost -$221.34 while the best gained only +$66.80, confirming poor risk asymmetry.

**Conclusion:** the fresh signal's mean return is centred near zero, the confidence interval includes zero, and the return distribution has negative skew. There is **no reliable midpoint edge**.

---

## 5. Remaining limitations and unresolved risks

1. **No true LEAN rerun.** Docker / .NET / `lean` CLI are unavailable locally, so the corrected backtest is a simulation derived from the export. A real rerun in LEAN is required before any capital decision.
2. **No minute-by-minute price data.** Forward returns at 1/5/15/30/60-minute horizons cannot be computed from the export; only entry-to-exit returns are available.
3. **Early-close corrected P&L is approximate.** We can count that the violations disappear, but we do not have 12:55 ET prices to revalue those exits.
4. **Execution model remains immediate.** A one-minute / next-bar delay, partial fills, and deeper market-impact modeling are documented but not fully implemented in code because they require intraday quote data or a more sophisticated fill model.
5. **RVOL baseline is loaded once per symbol entry.** It is not refreshed daily; this is pre-existing and could become stale.
6. **Universe survivorship and delistings.** The export does not include delisted names or point-in-time fundamental availability; this must be cleared before live use.
7. **Pattern-day-trader risk.** The strategy day-trades frequently in a ~$25k margin account. Regulatory limits must be verified with the intended broker.
8. **Parameter tuning was not performed.** The audit explicitly forbade tuning thresholds, periods, or filters on this backtest. None were changed.

---

## 6. Verdict

| Question | Verdict |
|---|---|
| Lifecycle defects corrected in source? | **Yes.** The algorithm now expires insights before flatten, uses `BeforeMarketClose`, blocks re-entry until a fresh signal, tags every order, and clears state at EOD. |
| Lifecycle regression tests pass? | **Yes.** 13/13 deterministic tests pass. |
| No stale insight can reopen a flattened position? | **Yes** (enforced by `SymbolLifecycle` and `lock_for_session_close`). |
| No early-close session creates unintended overnight holding? | **Yes** (scheduled flatten is exchange-calendar-aware). |
| No order generated after its signal expires? | **Yes** (`record_entry_order` raises if the signal is expired; execution model checks `is_signal_active`). |
| Signal edge supported after realistic costs? | **No.** Fresh-signal mean return is near zero, 95% CI includes zero, and net return is negative under every fee/slippage scenario tested. |
| Suitable for paper trading? | **No.** The strategy lacks a positive expected return after costs. Engineering correctness does not create an edge where none exists. |
| Suitable for live capital? | **Absolutely not.** |

**Bottom line:** the implementation bugs have been fixed and the lifecycle is now internally consistent, but the underlying RVOL/9 EMA signal does not demonstrate predictive value after realistic costs. Further research would require a genuinely new hypothesis, pre-registered rules, walk-forward validation, and an untouched final holdout—not parameter tuning on this same history.
