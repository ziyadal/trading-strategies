#!/usr/bin/env python3
"""Reproduce baseline backtest metrics from the original LEAN exports.

Reads:
  - Retrospective Fluorescent Orange Pigeon.json (report)
  - Retrospective Fluorescent Orange Pigeon_insights.json
  - Retrospective Fluorescent Orange Pigeon_orders.csv
  - Retrospective Fluorescent Orange Pigeon_trades.csv

Writes:
  - artifacts/baseline_metrics.json
"""
from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean, stdev

EXPORTS = Path("/mnt/c/Users/Ziyad/Downloads")
ARTIFACTS = Path("artifacts")
ARTIFACTS.mkdir(exist_ok=True)


def parse_utc(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def epoch_to_utc(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def load_report() -> dict:
    with open(EXPORTS / "Retrospective Fluorescent Orange Pigeon.json", "r", encoding="utf-8") as f:
        return json.load(f)


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
            # Order ids may be separated by commas and/or whitespace.
            ids = row.get("Order Ids", "").replace("\t", "").replace(" ", "")
            row["OrderIds"] = [int(x) for x in ids.split(",") if x]
            rows.append(row)
    return rows


def parse_orders(report: dict) -> dict[int, dict]:
    """Return a dict of order id -> normalized order record from the report JSON."""
    out = {}
    for raw in report["orders"].values():
        o = dict(raw)
        o["id"] = int(raw["id"])
        o["Time_dt"] = parse_utc(raw["time"])
        o["Symbol"] = raw["symbol"]["value"]
        o["Type"] = {0: "Market", 4: "Market On Open"}.get(raw["type"], str(raw["type"]))
        o["Direction"] = "Buy" if raw["direction"] == 0 else "Sell"
        o["Quantity_f"] = float(raw["quantity"])
        o["Price_f"] = float(raw["price"])
        o["Value_f"] = float(raw["value"])
        o["Tag"] = (raw.get("tag") or "").strip()
        sub = raw.get("orderSubmissionData") or {}
        o["Bid_f"] = float(sub["bidPrice"]) if sub.get("bidPrice") is not None else None
        o["Ask_f"] = float(sub["askPrice"]) if sub.get("askPrice") is not None else None
        o["Last_f"] = float(sub["lastPrice"]) if sub.get("lastPrice") is not None else None
        out[o["id"]] = o
    return out


def monthly_pnl(trades: list[dict]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for t in trades:
        key = t["Exit_dt"].strftime("%Y-%m")
        out[key] += t["P&L_f"] - t["Fees_f"]
    return dict(sorted(out.items()))


def pf(trades_subset: list[dict], net: bool = False) -> float | None:
    """Profit factor. CSV P&L is gross (before explicit fees); net = P&L - fees."""
    wins = sum(max(t["P&L_f"] - (t["Fees_f"] if net else 0.0), 0.0) for t in trades_subset)
    losses = sum(min(t["P&L_f"] - (t["Fees_f"] if net else 0.0), 0.0) for t in trades_subset)
    if losses == 0:
        return None
    return round(wins / abs(losses), 3)


def main() -> None:
    report = load_report()
    insights = load_insights()
    trades = load_trades()
    orders = parse_orders(report)

    stats = report["statistics"]

    # ---- Core account metrics ----
    start_equity = float(stats["Start Equity"])
    end_equity = float(stats["End Equity"])
    net_pnl = end_equity - start_equity
    net_return = float(stats["Net Profit"].replace("%", "")) / 100.0
    total_fees = float(stats["Total Fees"].replace("$", "").replace(",", ""))
    gross_pnl = net_pnl + total_fees  # CSV P&L sums to approximately this
    drawdown = float(stats["Drawdown"].replace("%", "")) / 100.0
    sharpe = float(stats["Sharpe Ratio"])

    # Benchmark change from report charts if present.
    benchmark_return = None
    if "charts" in report and "Benchmark" in report["charts"]:
        bench = report["charts"]["Benchmark"]
        if "series" in bench and "Values" in bench["series"]:
            vals = bench["series"]["Values"]["values"]
            if len(vals) >= 2:
                benchmark_return = (vals[-1]["y"] - vals[0]["y"]) / vals[0]["y"]

    # ---- Counts ----
    order_count = len(orders)
    trade_count = len(trades)
    insight_count = len(insights)
    symbols = {t["Symbols"] for t in trades}
    symbol_count = len(symbols)
    volume = sum(abs(o["Value_f"]) for o in orders.values())

    # ---- Insight keys ----
    insight_keys = {(i["ticker"], epoch_to_utc(i["generatedTime"])) for i in insights}

    # Map each trade to its originating insight (if any). A fresh trade maps by
    # exact (symbol, entry_time). A stale trade maps to the most recent prior
    # insight that was still active at entry (generated_time <= entry <= close_time).
    insights_by_symbol: dict[str, list[dict]] = defaultdict(list)
    for i in insights:
        i["_generated_dt"] = epoch_to_utc(i["generatedTime"])
        i["_close_dt"] = epoch_to_utc(i["closeTime"])
        insights_by_symbol[i["ticker"]].append(i)
    for sym_list in insights_by_symbol.values():
        sym_list.sort(key=lambda i: i["_generated_dt"])

    def find_originating_insight(t: dict) -> dict | None:
        sym_list = insights_by_symbol.get(t["Symbols"], [])
        best = None
        for i in sym_list:
            if i["_generated_dt"] <= t["Entry_dt"] <= i["_close_dt"]:
                best = i
            elif i["_generated_dt"] > t["Entry_dt"]:
                break
        return best

    # ---- Spread cost estimate (midpoint to touch) ----
    spread_cost = 0.0
    for o in orders.values():
        bid = o["Bid_f"]
        ask = o["Ask_f"]
        if bid is None or ask is None:
            continue
        mid = (bid + ask) / 2.0
        qty = abs(o["Quantity_f"])
        if o["Direction"] == "Buy":
            spread_cost += (ask - mid) * qty
        else:
            spread_cost += (mid - bid) * qty

    # ---- Trade categorisation ----
    fresh_trades = []
    stale_trades = []
    liquidated_count = 0
    liquidated_gross = 0.0
    liquidated_net = 0.0
    next_session_count = 0
    next_session_gross = 0.0
    next_session_net = 0.0
    additional_buys = 0
    moo_buys = 0

    for t in trades:
        entry_dt = t["Entry_dt"]
        symbol = t["Symbols"]
        is_fresh = (symbol, entry_dt) in insight_keys
        originating = find_originating_insight(t)

        entry_oid = t["OrderIds"][0] if t["OrderIds"] else None
        entry_order = orders.get(entry_oid) if entry_oid is not None else None
        if entry_order is not None and entry_order["Type"] == "Market On Open":
            moo_buys += 1

        if is_fresh:
            fresh_trades.append(t)
        else:
            stale_trades.append(t)
            if originating is not None and entry_dt.date() > originating["_generated_dt"].date():
                next_session_count += 1
                next_session_gross += t["P&L_f"]
                next_session_net += t["P&L_f"] - t["Fees_f"]

        if t["OrderIds"]:
            exit_oid = t["OrderIds"][-1]
            exit_order = orders.get(exit_oid)
            if exit_order is not None and exit_order["Tag"] == "Liquidated":
                liquidated_count += 1
                liquidated_gross += t["P&L_f"]
                liquidated_net += t["P&L_f"] - t["Fees_f"]

    # Additional buys without fresh signal.
    for o in orders.values():
        if o["Direction"] != "Buy":
            continue
        key = (o["Symbol"], o["Time_dt"])
        if key not in insight_keys:
            additional_buys += 1

    # ---- Fresh-only order/spread statistics ----
    fresh_order_ids = set()
    for t in fresh_trades:
        fresh_order_ids.update(t["OrderIds"])
    fresh_orders = [orders[oid] for oid in fresh_order_ids if oid in orders]
    fresh_order_count = len(fresh_orders)
    fresh_volume = sum(abs(o["Value_f"]) for o in fresh_orders)
    fresh_spread_cost = 0.0
    for o in fresh_orders:
        bid = o["Bid_f"]
        ask = o["Ask_f"]
        if bid is None or ask is None:
            continue
        mid = (bid + ask) / 2.0
        qty = abs(o["Quantity_f"])
        if o["Direction"] == "Buy":
            fresh_spread_cost += (ask - mid) * qty
        else:
            fresh_spread_cost += (mid - bid) * qty

    # ---- Early-close violations ----
    early_close_dates = {date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24)}
    early_close_violations = 0
    for o in orders.values():
        if o["Direction"] != "Sell":
            continue
        et = o["Time_dt"].astimezone(timezone(timedelta(hours=-5)))
        if et.date() in early_close_dates and et.time() > time(13, 0):
            early_close_violations += 1

    # ---- Monthly / recent performance ----
    months = monthly_pnl(trades)
    profitable_months = sum(1 for v in months.values() if v > 0)
    total_months = len(months)

    last_exit = max(t["Exit_dt"] for t in trades)
    cutoff_90 = last_exit.timestamp() - 90 * 24 * 3600
    last_90_trades = [t for t in trades if t["Exit_dt"].timestamp() > cutoff_90]
    last_90_net = sum(t["P&L_f"] - t["Fees_f"] for t in last_90_trades)
    last_90_pf = pf(last_90_trades, net=True)

    fresh_last_90_trades = [t for t in fresh_trades if t["Exit_dt"].timestamp() > cutoff_90]
    fresh_last_90_net = sum(t["P&L_f"] - t["Fees_f"] for t in fresh_last_90_trades)
    fresh_last_90_pf = pf(fresh_last_90_trades, net=True)

    # ---- Win rates ----
    gross_wins = sum(1 for t in trades if t["P&L_f"] > 0)
    net_wins = sum(1 for t in trades if t["P&L_f"] - t["Fees_f"] > 0)

    metrics = {
        "start_equity": start_equity,
        "end_equity": end_equity,
        "net_pnl_dollars": round(net_pnl, 2),
        "net_return_fraction": round(net_return, 4),
        "gross_pnl_dollars": round(gross_pnl, 2),
        "total_fees": round(total_fees, 2),
        "max_drawdown_fraction": round(drawdown, 4),
        "sharpe_ratio": sharpe,
        "benchmark_return_fraction": round(benchmark_return, 4) if benchmark_return is not None else None,
        "order_count": order_count,
        "trade_count": trade_count,
        "insight_count": insight_count,
        "symbol_count": symbol_count,
        "two_way_notional": round(volume, 2),
        "estimated_spread_cost": round(spread_cost, 2),
        "fresh_order_count": fresh_order_count,
        "fresh_two_way_notional": round(fresh_volume, 2),
        "fresh_estimated_spread_cost": round(fresh_spread_cost, 2),
        "additional_buys_without_fresh_signal": additional_buys,
        "market_on_open_buys": moo_buys,
        "early_close_violations": early_close_violations,
        "fresh_trade_count": len(fresh_trades),
        "fresh_gross_pnl": round(sum(t["P&L_f"] for t in fresh_trades), 2),
        "fresh_net_pnl": round(sum(t["P&L_f"] - t["Fees_f"] for t in fresh_trades), 2),
        "fresh_gross_profit_factor": pf(fresh_trades, net=False),
        "fresh_net_profit_factor": pf(fresh_trades, net=True),
        "stale_trade_count": len(stale_trades),
        "stale_net_pnl": round(sum(t["P&L_f"] - t["Fees_f"] for t in stale_trades), 2),
        "next_session_reentry_count": next_session_count,
        "next_session_reentry_gross_pnl": round(next_session_gross, 2),
        "next_session_reentry_net_pnl": round(next_session_net, 2),
        "liquidated_trade_count": liquidated_count,
        "liquidated_gross_pnl": round(liquidated_gross, 2),
        "liquidated_net_pnl": round(liquidated_net, 2),
        "profitable_months": profitable_months,
        "total_months": total_months,
        "last_90_day_trade_count": len(last_90_trades),
        "last_90_day_net_pnl": round(last_90_net, 2),
        "last_90_day_profit_factor": last_90_pf,
        "fresh_last_90_day_trade_count": len(fresh_last_90_trades),
        "fresh_last_90_day_net_pnl": round(fresh_last_90_net, 2),
        "fresh_last_90_day_profit_factor": fresh_last_90_pf,
        "gross_win_rate": round(gross_wins / trade_count, 4) if trade_count else 0,
        "net_win_rate": round(net_wins / trade_count, 4) if trade_count else 0,
        "monthly_pnl": {k: round(v, 2) for k, v in months.items()},
    }

    with open(ARTIFACTS / "baseline_metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)

    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
