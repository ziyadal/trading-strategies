# Quantitative Trading Strategy Research

A research workspace for translating discretionary trading ideas into explicit QuantConnect algorithms—and then trying to disprove them with lifecycle audits, regression tests, realistic costs, and reproducible analysis.

The most important result in this repository is not a profitable backtest. It is the demonstration that **engineering correctness and trading edge are separate questions**: a strategy can be repaired, fully tested, and still be unsuitable for paper or live trading.

## Projects

| Project | Focus | Status |
|---|---|---|
| [`rvol_9ema_long_no_depth/`](rvol_9ema_long_no_depth/) | Large-cap RVOL scan, 2m/5m 9 EMA pullback, signal lifecycle, costs | Audited; lifecycle defects fixed; no reliable edge found |
| [`opening_rvol_9ema_pullback/`](opening_rvol_9ema_pullback/) | Opening-window RVOL ranking with EMA support and ATR risk | Active research prototype |
| [`three_day_selloff_reclaim/`](three_day_selloff_reclaim/) | Three-day selloff followed by a 5-minute reclaim | Early prototype |
| [`vwap_pullback_entry/`](vwap_pullback_entry/) | VWAP touch-and-reclaim entry logic | Minimal experiment |

## Flagship audit: RVOL / 9 EMA

The original QuantConnect export contained two material lifecycle defects:

- a stale six-hour insight could reopen a position after the scheduled end-of-day flatten;
- a hard-coded 15:55 exit ignored exchange half-days and created unintended overnight exposure.

The audit traced orders back to signals, reproduced the failure, separated fresh-signal trades from stale re-entries, and added a pure-Python lifecycle state machine with **13 deterministic regression tests**.

### Verified audit findings

- 1,285 buys occurred without a fresh signal; 1,247 were next-session re-entries.
- The original export lost 32.39% after explicit fees.
- Removing stale-lifecycle trades improved the simulated result by 17.38 percentage points, but the fresh signal still lost money after costs.
- The 95% bootstrap confidence interval for mean gross return included zero.
- Final verdict: engineering defects corrected; predictive edge not demonstrated; unsuitable for paper or live capital.

Read the full [`IMPROVEMENT_REPORT.md`](rvol_9ema_long_no_depth/IMPROVEMENT_REPORT.md) and machine-readable evidence in [`artifacts/`](rvol_9ema_long_no_depth/artifacts/).

## Reproduce the engineering checks

From `rvol_9ema_long_no_depth/`:

```bash
python -m unittest tests.test_lifecycle -v
python -m py_compile long_rvol_9ema_framework_annotated.py lifecycle.py
python scripts/reproduce_baseline.py
python scripts/simulate_corrected.py
python scripts/signal_thesis.py
```

The corrected performance table is an approximation derived from the original export, not a true LEAN rerun. The detailed report documents this and the remaining data, execution, survivorship, and regulatory limitations.

Research only. No strategy here is approved for live capital, and nothing in this repository is financial advice.

