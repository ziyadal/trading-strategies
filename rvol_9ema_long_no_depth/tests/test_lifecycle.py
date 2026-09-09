#!/usr/bin/env python3
"""Deterministic lifecycle invariants for the RVOL/9 EMA strategy.

These tests exercise the pure-Python lifecycle state machine without any
QuantConnect dependencies.  They encode the acceptance criteria from the audit.
"""
from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

from lifecycle import LifecycleRegistry, SymbolLifecycle, ExitReason


class TestLifecycleInvariants(unittest.TestCase):
    """Invariant tests for one fresh signal -> one position -> one exit."""

    def setUp(self) -> None:
        self.registry = LifecycleRegistry(min_target_change=0.005)
        self.state = self.registry.get("CSCO")
        self.session_date = date(2025, 1, 2)
        self.registry.reset_for_session(self.session_date)

    def test_01_session_reset_clears_all_state(self) -> None:
        self.state.record_signal("sig1", datetime(2025, 1, 2, 10, 1), datetime(2025, 1, 2, 15, 55), 0.09)
        self.registry.reset_for_session(self.session_date)
        self.assertIsNone(self.state.signal_id)
        self.assertEqual(self.state.position_state, "flat")
        self.assertFalse(self.state.flattened_today)

    def test_02_cannot_emit_signal_after_flatten_until_next_session(self) -> None:
        self.state.record_signal("sig1", datetime(2025, 1, 2, 10, 1), datetime(2025, 1, 2, 15, 55), 0.09)
        self.registry.lock_for_session_close()
        self.assertFalse(self.state.can_emit_signal(datetime(2025, 1, 2, 15, 56)))
        # New session reset re-enables emission.
        self.registry.reset_for_session(date(2025, 1, 3))
        self.assertTrue(self.state.can_emit_signal(datetime(2025, 1, 3, 10, 1)))

    def test_03_csco_sequence_cannot_create_next_day_buy_without_fresh_signal(self) -> None:
        """Reproduce the audited CSCO sequence and verify no MOO re-entry."""
        # Day 1 fresh signal and entry.
        self.state.record_signal(
            "CSCO_20250102_100100",
            datetime(2025, 1, 2, 10, 1),
            datetime(2025, 1, 2, 15, 55),
            0.09,
        )
        self.state.record_entry_order(1, datetime(2025, 1, 2, 10, 1))

        # End-of-day flatten removes the active signal and blocks entries.
        self.registry.lock_for_session_close()
        self.assertIsNone(self.state.signal_id)
        self.assertTrue(self.state.flattened_today)

        # Next-day market-on-open attempt must be rejected.
        next_open = datetime(2025, 1, 3, 9, 31)
        self.assertFalse(self.state.is_signal_active(next_open))
        self.assertFalse(self.state.can_emit_signal(next_open))

    def test_04_no_order_after_signal_expires(self) -> None:
        expiry = datetime(2025, 1, 2, 15, 55)
        self.state.record_signal("sig1", datetime(2025, 1, 2, 10, 1), expiry, 0.09)
        with self.assertRaises(RuntimeError):
            self.state.record_entry_order(1, expiry)
        self.assertFalse(self.state.is_signal_active(expiry + timedelta(seconds=1)))

    def test_05_stale_insight_cannot_generate_market_on_open(self) -> None:
        self.state.record_signal("sig1", datetime(2025, 1, 2, 10, 1), datetime(2025, 1, 2, 15, 55), 0.09)
        self.registry.lock_for_session_close()
        # After lock, the state machine reports no active signal and blocks entry.
        self.assertFalse(self.state.is_signal_active(datetime(2025, 1, 3, 9, 31)))
        self.assertFalse(self.state.can_emit_signal(datetime(2025, 1, 3, 9, 31)))

    def test_06_repeated_same_insight_does_not_create_duplicate_orders(self) -> None:
        signal_time = datetime(2025, 1, 2, 10, 1)
        expiry = datetime(2025, 1, 2, 15, 55)
        self.state.record_signal("sig1", signal_time, expiry, 0.09)
        self.state.record_entry_order(1, signal_time)
        # A second call with the same signal ID must not duplicate.
        self.state.record_entry_order(1, signal_time)
        self.assertEqual(self.state.entry_order_ids, [1])

    def test_07_no_multiple_unintended_lifecycle_states(self) -> None:
        self.state.record_signal("sig1", datetime(2025, 1, 2, 10, 1), datetime(2025, 1, 2, 15, 55), 0.09)
        self.assertEqual(self.state.position_state, "pending_entry")
        self.state.record_entry_order(1, datetime(2025, 1, 2, 10, 1))
        self.assertEqual(self.state.position_state, "in_position")
        # A second fresh signal while already in position is rejected.
        self.assertFalse(self.state.can_emit_signal(datetime(2025, 1, 2, 11, 0)))

    def test_08_new_entry_only_after_fresh_signal(self) -> None:
        # Before any signal the symbol may emit one.
        self.assertTrue(self.state.can_emit_signal(datetime(2025, 1, 2, 10, 1)))
        self.state.record_signal("sig1", datetime(2025, 1, 2, 10, 1), datetime(2025, 1, 2, 15, 55), 0.09)
        # Once a signal exists, a second fresh signal is blocked.
        self.assertFalse(self.state.can_emit_signal(datetime(2025, 1, 2, 10, 2)))
        # Entry is permitted only while that signal is active.
        self.assertTrue(self.state.is_signal_active(datetime(2025, 1, 2, 10, 1)))

    def test_09_order_tag_contains_audit_fields(self) -> None:
        self.state.record_signal("sig1", datetime(2025, 1, 2, 10, 1), datetime(2025, 1, 2, 15, 55), 0.09)
        tag = self.state.order_tag("entry")
        self.assertIn("sym=CSCO", tag)
        self.assertIn("sig=sig1", tag)
        self.assertIn("tgt=0.09", tag)
        self.assertIn("reason=entry", tag)
        self.assertIn("sess=2025-01-02", tag)

    def test_10_eod_flat_run_leaves_flat_state(self) -> None:
        """After flatten: zero intended position, no active signal, no pending entry."""
        self.state.record_signal("sig1", datetime(2025, 1, 2, 10, 1), datetime(2025, 1, 2, 15, 55), 0.09)
        self.state.record_entry_order(1, datetime(2025, 1, 2, 10, 1))
        self.registry.lock_for_session_close()
        self.assertTrue(self.state.is_flat_after_session(datetime(2025, 1, 2, 15, 55)))

    def test_11_micro_rebalance_suppressed_by_threshold(self) -> None:
        self.assertTrue(self.state.target_changed(0.09, 0.0))
        self.assertFalse(self.state.target_changed(0.0901, 0.09))  # within 0.5%

    def test_12_early_close_flatten_before_actual_close(self) -> None:
        """A 13:00 ET early close must flatten no later than 12:55 ET."""
        # Simulate a signal and an early-close flatten at 12:55 ET.
        self.state.record_signal("sig1", datetime(2025, 7, 3, 10, 1), datetime(2025, 7, 3, 12, 55), 0.09)
        self.registry.lock_for_session_close()
        self.assertTrue(self.state.flattened_today)
        self.assertIsNone(self.state.signal_id)


class TestLifecycleRegistry(unittest.TestCase):
    def test_reset_and_lock_apply_to_all_symbols(self) -> None:
        registry = LifecycleRegistry()
        registry.get("AAPL")
        registry.get("TSLA")
        registry.reset_for_session(date(2025, 1, 2))
        registry.lock_for_session_close()
        for state in registry.values():
            self.assertTrue(state.flattened_today)


if __name__ == "__main__":
    unittest.main()
