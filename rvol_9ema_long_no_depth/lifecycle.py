"""Pure-Python per-symbol lifecycle state for the intraday RVOL/9 EMA strategy.

This module has no QuantConnect dependencies so it can be unit-tested outside
LEAN.  It tracks one fresh signal → one intended position → one auditable exit,
including the end-of-day flatten, session reset, and order-ID reconciliation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any


class ExitReason(Enum):
    """Discrete reasons emitted on every exit order tag."""

    EOD = "eod"
    PROFIT_TARGET = "profit_target"
    MAX_LOSS = "max_loss"
    EXPIRY = "expiry"
    REBALANCE = "rebalance"
    MANUAL = "manual"


@dataclass
class SymbolLifecycle:
    """State machine for a single symbol's intraday lifecycle.

    Invariants enforced:
      - Only one fresh signal may be active at a time.
      - Once a session is flattened, no new entry is allowed until the next session.
      - No order may be generated after the active signal has expired.
      - Every exit carries an explicit reason.
    """

    symbol: str
    session_date: date | None = None
    signal_id: str | None = None
    signal_time: datetime | None = None
    signal_expiry: datetime | None = None
    intended_target: float = 0.0
    entry_order_ids: list[int] = field(default_factory=list)
    exit_order_ids: list[int] = field(default_factory=list)
    exit_reason: ExitReason | None = None
    flattened_today: bool = False
    position_state: str = "flat"  # flat, pending_entry, in_position, pending_exit

    # Minimum notional change (fraction of portfolio) before an execution model
    # should submit a rebalance order.  Used to suppress micro-rebalances.
    min_target_change: float = 0.005

    def reset_for_session(self, session_date: date) -> None:
        """Called by the alpha model at the first bar of a new session."""
        self.session_date = session_date
        self.signal_id = None
        self.signal_time = None
        self.signal_expiry = None
        self.intended_target = 0.0
        self.entry_order_ids.clear()
        self.exit_order_ids.clear()
        self.exit_reason = None
        self.flattened_today = False
        self.position_state = "flat"

    def lock_for_session_close(self) -> None:
        """Called by the end-of-day flatten routine.

        Invalidates any active signal and blocks further entries until the next
        session reset.
        """
        self.flattened_today = True
        self.signal_id = None
        self.signal_time = None
        self.signal_expiry = None
        self.intended_target = 0.0
        self.position_state = "flat" if self.position_state in ("flat", "in_position") else self.position_state
        self.exit_reason = ExitReason.EOD

    def can_emit_signal(self, now: datetime) -> bool:
        """True when the symbol is allowed to emit a fresh long signal."""
        if self.session_date is None or now.date() != self.session_date:
            return False
        if self.flattened_today:
            return False
        if self.signal_id is not None:
            return False
        if self.position_state != "flat":
            return False
        return True

    def record_signal(
        self,
        signal_id: str,
        signal_time: datetime,
        signal_expiry: datetime,
        intended_target: float,
    ) -> None:
        """Record a newly emitted fresh signal."""
        if signal_expiry <= signal_time:
            raise ValueError("signal expiry must be after signal time")
        self.signal_id = signal_id
        self.signal_time = signal_time
        self.signal_expiry = signal_expiry
        self.intended_target = intended_target
        self.position_state = "pending_entry"
        self.exit_reason = None

    def record_entry_order(self, order_id: int, now: datetime) -> None:
        """Record that an entry order was submitted for the active signal."""
        if not self.is_signal_active(now):
            raise RuntimeError("entry order submitted after signal expired")
        if order_id not in self.entry_order_ids:
            self.entry_order_ids.append(order_id)
        self.position_state = "in_position"

    def record_exit_order(self, order_id: int, reason: ExitReason, now: datetime) -> None:
        """Record that an exit order was submitted."""
        if order_id not in self.exit_order_ids:
            self.exit_order_ids.append(order_id)
        self.exit_reason = reason
        self.position_state = "pending_exit"

    def confirm_exit(self) -> None:
        """Called when the exit order is fully filled."""
        self.position_state = "flat"
        self.signal_id = None
        self.signal_time = None
        self.signal_expiry = None
        self.intended_target = 0.0

    def is_signal_active(self, now: datetime) -> bool:
        """True if a fresh signal exists and has not expired."""
        if self.signal_id is None or self.signal_time is None or self.signal_expiry is None:
            return False
        return self.signal_time <= now < self.signal_expiry

    def is_flat_after_session(self, now: datetime) -> bool:
        """Used by EOD invariants: flat, no active signal, no pending entry."""
        return (
            self.position_state in ("flat", "pending_exit")
            and self.signal_id is None
            and not self.is_signal_active(now)
        )

    def order_tag(self, reason: str, extra: dict[str, Any] | None = None) -> str:
        """Build an auditable reason tag for an order."""
        parts = [
            f"sym={self.symbol}",
            f"sig={self.signal_id or 'none'}",
            f"sig_t={self.signal_time.isoformat() if self.signal_time else 'none'}",
            f"tgt={self.intended_target:.4f}",
            f"sess={self.session_date.isoformat() if self.session_date else 'none'}",
            f"reason={reason}",
        ]
        if extra:
            for k, v in extra.items():
                parts.append(f"{k}={v}")
        return "|".join(parts)

    def target_changed(self, new_target: float, current_holding_percent: float) -> bool:
        """True when the proposed target differs enough from current holdings."""
        return abs(new_target - current_holding_percent) >= self.min_target_change


class LifecycleRegistry:
    """Container holding one SymbolLifecycle per tracked ticker."""

    def __init__(self, min_target_change: float = 0.005) -> None:
        self._states: dict[str, SymbolLifecycle] = {}
        self._min_target_change = min_target_change

    def get(self, symbol: str) -> SymbolLifecycle:
        if symbol not in self._states:
            self._states[symbol] = SymbolLifecycle(symbol=symbol, min_target_change=self._min_target_change)
        return self._states[symbol]

    def reset_for_session(self, session_date: date) -> None:
        for state in self._states.values():
            state.reset_for_session(session_date)

    def lock_for_session_close(self) -> None:
        for state in self._states.values():
            state.lock_for_session_close()

    def values(self) -> list[SymbolLifecycle]:
        return list(self._states.values())

    def symbols(self) -> list[str]:
        return list(self._states.keys())
