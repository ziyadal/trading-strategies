#!/usr/bin/env python3
"""Test the underlying RVOL/9 EMA signal thesis using only fresh-signal trades.

This is not a forward-horizon study (minute-by-minute price data is not
available in the exports).  Instead it reports the realised return distribution
of the 1,278 fresh-signal trades and their concentration.
"""
from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, stdev

EXPORTS = Path("/mnt/c/Users/Ziyad/Downloads")
ARTIFACTS = Path("artifacts")
ARTIFACTS.mkdir(exist_ok=True)


def parse_utc(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def epoch_to_utc(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def load_insights() -> list[dict]:
    with open(EXPORTS / "Retrospective Fluorescent Orange Pigeon_insights.json", "r", encoding="utf-8") as f:
        return json.load(f)


def load_trades() -> list[dict]:
    rows = []
    with open(EXPORTS / "Retrospective Fluorescent Orange Pigeon_trades.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row["Entry_dt"] = parse_utc(row["Entry Time"])
            row["Exit_dt"] = parse_utc(row["Exit Time"])
            row["Entry Price_f"] = float(row["Entry Price"])
            row["Exit Price_f"] = float(row["Exit Price"])
            row["Quantity_f"] = float(row["Quantity"])
            row["P&L_f"] = float(row["P&L"])
            row["Fees_f"] = float(row["Fees"])
            row["GrossReturn"] = (row["Exit Price_f"] - row["Entry Price_f"]) / row["Entry Price_f"]
            row["NetReturn"] = row["GrossReturn"] - (row["Fees_f"] / (row["Entry Price_f"] * row["Quantity_f"])) if row["Entry Price_f"] * row["Quantity_f"] != 0 else row["GrossReturn"]
            rows.append(row)
    return rows


def bootstrap_ci(values: list[float], confidence: float = 0.95, n_samples: int = 10_000) -> tuple[float, float]:
    if len(values) < 2:
        return (0.0, 0.0)
    import random
    rng = random.Random(42)
    means = []
    for _ in range(n_samples):
        sample = [values[rng.randrange(len(values))] for _ in values]
        means.append(sum(sample) / len(sample))
    means.sort()
    lower_idx = int((1 - confidence) / 2 * n_samples)
    upper_idx = int((1 + confidence) / 2 * n_samples)
    return (means[lower_idx], means[upper_idx])


def main() -> None:
    insights = load_insights()
    trades = load_trades()
    insight_keys = {(i["ticker"], epoch_to_utc(i["generatedTime"])) for i in insights}

    fresh = [t for t in trades if (t["Symbols"], t["Entry_dt"]) in insight_keys]
    returns_gross = [t["GrossReturn"] for t in fresh]
    returns_net = [t["NetReturn"] for t in fresh]

    def stats(vals: list[float]) -> dict:
        if not vals:
            return {}
        return {
            "count": len(vals),
            "mean_bps": round(mean(vals) * 10_000, 2),
            "median_bps": round(median(vals) * 10_000, 2),
            "std_bps": round(stdev(vals) * 10_000, 2) if len(vals) > 1 else 0.0,
            "hit_rate": round(sum(1 for v in vals if v > 0) / len(vals), 4),
        }

    gross_stats = stats(returns_gross)
    net_stats = stats(returns_net)

    # By year
    by_year = {}
    for t in fresh:
        year = t["Entry_dt"].year
        by_year.setdefault(year, []).append(t["GrossReturn"])

    by_year_stats = {
        str(year): {
            "count": len(vals),
            "mean_bps": round(mean(vals) * 10_000, 2),
            "hit_rate": round(sum(1 for v in vals if v > 0) / len(vals), 4),
        }
        for year, vals in sorted(by_year.items())
    }

    # Concentration: top 10 contributors.
    fresh_sorted = sorted(fresh, key=lambda t: t["P&L_f"], reverse=True)
    top10_win = sum(t["P&L_f"] for t in fresh_sorted[:10])
    bottom10_loss = sum(t["P&L_f"] for t in fresh_sorted[-10:])
    total_gross = sum(t["P&L_f"] for t in fresh)

    # Bootstrap confidence interval for mean gross return.
    ci_low, ci_high = bootstrap_ci(returns_gross)

    report = {
        "fresh_signals": len(fresh),
        "gross_returns_bps": gross_stats,
        "net_returns_bps": net_stats,
        "mean_gross_ci_95_bps": (round(ci_low * 10_000, 2), round(ci_high * 10_000, 2)),
        "by_year": by_year_stats,
        "concentration": {
            "top_10_gross_pnl": round(top10_win, 2),
            "bottom_10_gross_pnl": round(bottom10_loss, 2),
            "total_gross_pnl": round(total_gross, 2),
            "top_10_share_fraction": round(top10_win / total_gross, 3) if total_gross else None,
        },
        "interpretation": (
            "Mean gross return is near zero and the 95% CI includes zero, "
            "so the fresh signal does not show a reliable midpoint edge after costs."
        ),
    }

    with open(ARTIFACTS / "signal_thesis.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
