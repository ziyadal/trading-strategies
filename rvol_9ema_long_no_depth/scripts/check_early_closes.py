#!/usr/bin/env python3
"""Detect early-close violations and unintended overnight holdings.

Hard-codes the three NYSE early closes as regression examples, then reports any
sell orders submitted after the actual close on those sessions.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

EXPORTS = Path("/mnt/c/Users/Ziyad/Downloads")

EARLY_CLOSES = {
    date(2025, 7, 3): time(13, 0),
    date(2025, 11, 28): time(13, 0),
    date(2025, 12, 24): time(13, 0),
}


def parse_utc(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def to_et(dt: datetime) -> datetime:
    return dt.astimezone(timezone(timedelta(hours=-5)))


def main() -> None:
    with open(EXPORTS / "Retrospective Fluorescent Orange Pigeon.json", "r", encoding="utf-8") as f:
        report = json.load(f)

    violations = []
    for raw in report["orders"].values():
        if raw["direction"] != 1:  # only sell/liquidation orders matter here
            continue
        dt = parse_utc(raw["time"])
        et = to_et(dt)
        close = EARLY_CLOSES.get(et.date())
        if close and et.time() > close:
            violations.append({
                "order_id": raw["id"],
                "symbol": raw["symbol"]["value"],
                "submitted_utc": dt.isoformat(),
                "submitted_et": et.isoformat(),
                "scheduled_close_et": close.isoformat(),
                "tag": (raw.get("tag") or "").strip(),
                "type": {0: "Market", 4: "Market On Open"}.get(raw["type"], str(raw["type"])),
            })

    print(f"NYSE early-close violations (sells after actual 13:00 ET close): {len(violations)}")
    for v in violations:
        print(v)


if __name__ == "__main__":
    from datetime import date
    main()
