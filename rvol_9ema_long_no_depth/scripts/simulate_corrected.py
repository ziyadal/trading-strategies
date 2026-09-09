#!/usr/bin/env python3
"""Simulate the corrected backtest by removing stale lifecycle trades.

Because the LEAN engine is not available locally, this script derives a
"corrected" outcome from the original export under the assumption that the
lifecycle fixes eliminate every trade that was not opened by a fresh insight.

The result is an approximation, not a true rerun.  It is useful for:
  - confirming the magnitude of the stale-re-entry drag,
  - estimating the corrected order/fee/spread counts,
  - producing a defensible before/after comparison table.
"""
from __future__ import annotations

import json
from pathlib import Path

ARTIFACTS = Path("artifacts")


def main() -> None:
    with open(ARTIFACTS / "baseline_metrics.json", "r", encoding="utf-8") as f:
        baseline = json.load(f)

    # Corrected = fresh-signal trades only.  Stale re-entries and additional
    # same-day buys are removed because the lifecycle state machine blocks them.
    corrected = {
        "signals": baseline["insight_count"],
        "trades": baseline["fresh_trade_count"],
        "physical_orders": baseline["fresh_order_count"],
        "additional_buys_without_fresh_signals": 0,
        "market_on_open_orders": 0,
        "overnight_positions_carried": 0,
        "early_close_violations": 0,
        "gross_pnl": baseline["fresh_gross_pnl"],
        "estimated_spread_cost": baseline["fresh_estimated_spread_cost"],
        "explicit_fees": baseline["fresh_order_count"] * 1.0,  # $1/order baseline
        "net_pnl": baseline["fresh_net_pnl"],
        "net_return_fraction": round(baseline["fresh_net_pnl"] / baseline["start_equity"], 4),
        "max_drawdown_fraction": None,  # Cannot be reconstructed without equity curve
        "gross_profit_factor": baseline["fresh_gross_profit_factor"],
        "net_profit_factor": baseline["fresh_net_profit_factor"],
        "gross_win_rate": None,  # filled below
        "turnover_fraction": round(baseline["fresh_two_way_notional"] / baseline["start_equity"], 2),
        "last_90_day_trades": baseline["fresh_last_90_day_trade_count"],
        "last_90_day_net_pnl": baseline["fresh_last_90_day_net_pnl"],
        "last_90_day_profit_factor": baseline["fresh_last_90_day_profit_factor"],
    }

    # Approximate win rate from the fresh profit factor (PF = wins/|losses|)
    # and net PF.  We cannot recover the exact count without trade-level data
    # here, so we leave placeholders and note the limitation.
    corrected["gross_win_rate"] = None
    corrected["net_win_rate"] = None

    # ---- Cost stress tests (all based on fresh-signal trades) ----
    fresh_notional = baseline["fresh_two_way_notional"]
    fresh_fees = corrected["explicit_fees"]
    fresh_net = corrected["net_pnl"]

    stresses = {
        "baseline_1usd_fee_2bps_slippage": {
            "fee_per_order": 1.0,
            "slippage_bps": 2.0,
            "net_pnl": round(fresh_net, 2),
        },
        "schwab_0usd_fee_2bps_slippage": {
            "fee_per_order": 0.0,
            "slippage_bps": 2.0,
            "net_pnl": round(fresh_net + fresh_fees, 2),
        },
        "higher_slippage_5bps": {
            "fee_per_order": 1.0,
            "slippage_bps": 5.0,
            "net_pnl": round(fresh_net - (fresh_notional * 0.0005 * 2), 2),
        },
        "two_times_fee": {
            "fee_per_order": 2.0,
            "slippage_bps": 2.0,
            "net_pnl": round(fresh_net - fresh_fees, 2),
        },
    }

    output = {
        "corrected": corrected,
        "cost_stress_tests": stresses,
        "note": "Approximate simulation.  True rerun requires LEAN with the corrected algorithm.",
    }

    with open(ARTIFACTS / "corrected_metrics.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
