#!/usr/bin/env python3
"""Trace the first CSCO signal through the original export lifecycle.

Shows: signal -> insight -> target -> order -> fill -> position -> exit,
and explains which component generated the next-session market-on-open buy.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

EXPORTS = Path("/mnt/c/Users/Ziyad/Downloads")


def parse_utc(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def epoch_to_utc(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def main() -> None:
    with open(EXPORTS / "Retrospective Fluorescent Orange Pigeon.json", "r", encoding="utf-8") as f:
        report = json.load(f)
    with open(EXPORTS / "Retrospective Fluorescent Orange Pigeon_insights.json", "r", encoding="utf-8") as f:
        insights = json.load(f)
    with open(EXPORTS / "Retrospective Fluorescent Orange Pigeon_trades.csv", newline="", encoding="utf-8") as f:
        trades = list(csv.DictReader(f))

    orders = {}
    for raw in report["orders"].values():
        oid = int(raw["id"])
        orders[oid] = {
            "id": oid,
            "time": parse_utc(raw["time"]),
            "symbol": raw["symbol"]["value"],
            "type": {0: "Market", 4: "Market On Open"}.get(raw["type"], str(raw["type"])),
            "direction": "Buy" if raw["direction"] == 0 else "Sell",
            "quantity": float(raw["quantity"]),
            "price": float(raw["price"]),
            "tag": (raw.get("tag") or "").strip(),
        }

    # Find first CSCO insight.
    csco_insights = [i for i in insights if i["ticker"] == "CSCO"]
    csco_insights.sort(key=lambda i: i["generatedTime"])
    first = csco_insights[0]

    gen_dt = epoch_to_utc(first["generatedTime"])
    close_dt = epoch_to_utc(first["closeTime"])
    print(f"First CSCO insight")
    print(f"  id          : {first['id']}")
    print(f"  generated   : {gen_dt} UTC / {gen_dt.astimezone(timezone(timedelta(hours=-5)))} ET")
    print(f"  close       : {close_dt} UTC / {close_dt.astimezone(timezone(timedelta(hours=-5)))} ET")
    print(f"  period (s)  : {first['period']}  ({first['period']/3600:.1f} hours)")
    print(f"  weight      : {first['weight']}")
    print()

    # Orders tied to this insight by the exact generated timestamp (fresh entry)
    # plus all CSCO orders on the same/next days for context.
    fresh_entry_orders = [
        o for o in orders.values()
        if o["symbol"] == "CSCO" and o["time"] == gen_dt and o["direction"] == "Buy"
    ]
    print(f"Fresh buy order(s) emitted at insight time:")
    for o in fresh_entry_orders:
        print(f"  order {o['id']:>4}  {o['time']} UTC  {o['type']:15}  {o['direction']:4}  qty={o['quantity']:>3}  price={o['price']:.4f}  tag='{o['tag']}'")
    print()

    # CSCO trades that include those order ids.
    csco_trades = [t for t in trades if t["Symbols"] == "CSCO"]
    print("CSCO trades around the first signal:")
    for t in csco_trades[:6]:
        ids = t["Order Ids"].replace("\t", "").replace(" ", "")
        oid_list = [int(x) for x in ids.split(",") if x]
        entry = orders[oid_list[0]]
        exit_ = orders[oid_list[-1]]
        print(f"  entry {t['Entry Time']} -> exit {t['Exit Time']}")
        print(f"    entry order {entry['id']} {entry['type']} {entry['direction']} qty={entry['quantity']} @ {entry['price']:.4f} tag='{entry['tag']}'")
        print(f"    exit  order {exit_['id']} {exit_['type']} {exit_['direction']} qty={exit_['quantity']} @ {exit_['price']:.4f} tag='{exit_['tag']}'")
        print(f"    P&L gross={t['P&L']}  fees={t['Fees']}")
    print()

    # Identify the MOO re-entry order and the insight that caused it.
    moo_orders = [o for o in orders.values() if o["symbol"] == "CSCO" and o["type"] == "Market On Open"]
    if moo_orders:
        moo = min(moo_orders, key=lambda o: o["time"])
        print(f"First next-session MOO buy for CSCO:")
        print(f"  order {moo['id']}  {moo['time']} UTC  qty={moo['quantity']}  price={moo['price']:.4f}")
        # Which prior insight was still active at this time?
        active = [i for i in csco_insights if epoch_to_utc(i["generatedTime"]) <= moo["time"] <= epoch_to_utc(i["closeTime"])]
        if active:
            src = max(active, key=lambda i: i["generatedTime"])
            src_gen = epoch_to_utc(src["generatedTime"])
            print(f"  Still-active insight at that time: {src['id']} generated {src_gen} UTC")
            print(f"  That insight's close time ({epoch_to_utc(src['closeTime'])} UTC) is after the MOO fill.")
        print()

    print("Root-cause explanation:")
    print("  1. The alpha emits an Insight with a six-hour period.")
    print("  2. The scheduled flatten liquidates the position at 15:55 ET.")
    print("  3. The insight is NOT expired/removed by the flatten event.")
    print("  4. PortfolioConstruction sees the still-active insight before the next open")
    print("     and emits a target; ImmediateExecutionModel submits a Market-On-Open order.")
    print("  5. The position is sold a few minutes later when the old insight finally expires.")
    print("  Component responsible: the missing invalidation of the active insight at end-of-day flatten.")


if __name__ == "__main__":
    from datetime import timedelta
    main()
