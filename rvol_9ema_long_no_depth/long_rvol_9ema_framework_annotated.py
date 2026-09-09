from AlgorithmImports import *  # Import QuantConnect's core algorithm, data, indicator, and framework types.

from collections import defaultdict, deque  # defaultdict stores grouped volume history; deque stores recent bars efficiently.
from dataclasses import dataclass  # dataclass gives us a compact container for bar snapshots.
from datetime import time, timedelta  # time is used for market-clock checks; timedelta is used for consolidators and horizons.
import math  # math is used for NaN checks on fundamental values.
import uuid  # uuid generates unique signal IDs for audit tags.

from lifecycle import LifecycleRegistry, SymbolLifecycle, ExitReason  # Pure-Python lifecycle state machine.


# ---------------------------------------------------------------------------
# Parameter documentation
# ---------------------------------------------------------------------------
# The strategy is intentionally intraday and flat before every market close.
# All configurable knobs are declared here so the backtest is reproducible.
#
# Bar resolution: 1-minute from the universe; 2-minute and 5-minute consolidators
#   are built locally for the EMA/pullback logic.
# EMA definition: 9-period exponential moving average on the 2-minute and
#   5-minute consolidated closes.  Indicators are reset each session.
# RVOL definition: cumulative volume since 09:30 ET divided by the average
#   cumulative volume at the same time-of-day over the previous 20 trading
#   sessions.
# Universe: up to 50 large-cap, profitable, liquid U.S. equities.
# Scan: once per session at 09:45 ET; top 10 names by RVOL that are also above
#   the session open are eligible.
# Entry signal: first orderly pullback to the 9 EMA on the 2-minute chart
#   (backup: 5-minute after 2-minute setup invalidates), then a breakout above
#   the pullback-bar high.
# Insight horizon: capped to the same-session flatten time; never allowed to
#   spill into the next session.
# Position sizing: 9% target weight per signal, 10% cash buffer.
# Rebalance/flatten: five minutes before the exchange's actual close, including
#   early closes.
# ---------------------------------------------------------------------------


@dataclass  # This makes the class below a simple structured container.
class BarSnapshot:
    end_time: datetime  # The timestamp when the consolidated bar closes.
    open: float  # The bar's opening price.
    high: float  # The highest price reached during the bar.
    low: float  # The lowest price reached during the bar.
    close: float  # The bar's closing price.
    volume: float  # The total traded volume for the bar.
    ema: float  # The 9 EMA value associated with the bar timeframe.


class FixedFeePerOrderModel(FeeModel):
    """Configurable fixed-dollar fee per order.

    Default is $1/order so the corrected backtest stays comparable to the audit.
    Set to 0 to model commission-free brokers such as Charles Schwab.
    """

    def __init__(self, fee_per_order: float = 1.0) -> None:
        super().__init__()
        self._fee_per_order = float(fee_per_order)

    def get_order_fee(self, parameters):
        return OrderFee(CashAmount(self._fee_per_order, parameters.security.quote_currency.symbol))


class PercentSlippageModel(SlippageModel):
    """Slippage equal to a fixed percentage of the current quoted price."""

    def __init__(self, slippage_percent: float = 0.0002) -> None:
        super().__init__()
        self._slippage_percent = float(slippage_percent)

    def get_slippage_approximation(self, asset, order):
        return asset.price * self._slippage_percent


class LifecycleExecutionModel(ExecutionModel):
    """Execution model that ties every order to the symbol's lifecycle state.

    It suppresses micro-rebalances via a minimum target-change threshold and
    refuses to submit entry orders unless a fresh, unexpired insight is active.
    Every order is tagged with the originating signal ID, timestamp, target
    weight, session date, and reason.
    """

    def __init__(self, lifecycle_registry: LifecycleRegistry, min_target_change: float = 0.005) -> None:
        super().__init__()
        self._registry = lifecycle_registry
        self._min_target_change = min_target_change

    def execute(self, algorithm: QCAlgorithm, targets: list[PortfolioTarget]) -> None:
        if algorithm.is_warming_up:
            return

        total_value = algorithm.portfolio.total_portfolio_value
        for target in targets:
            symbol = target.symbol
            ticker = symbol.value
            state = self._registry.get(ticker)
            security = algorithm.securities[symbol]

            current_pct = security.holdings.holding_value / total_value if total_value > 0 else 0.0
            if abs(target.percent - current_pct) < self._min_target_change:
                # Suppress economically meaningless micro-rebalances.
                continue

            if target.percent == 0:
                # Exit target from risk model or end-of-day flatten.
                if security.holdings.quantity == 0:
                    continue
                reason = (state.exit_reason or ExitReason.MANUAL).value
                tag = state.order_tag(reason)
                ticket = algorithm.market_order(symbol, -security.holdings.quantity, tag=tag)
                if ticket is not None:
                    order_id = getattr(ticket, "order_id", getattr(ticket, "OrderId", None))
                    if order_id is not None:
                        state.record_exit_order(order_id, state.exit_reason or ExitReason.MANUAL, algorithm.time)
                continue

            # Entry target.  Only allowed while a fresh signal is alive and the
            # session has not been flattened.
            if state.flattened_today:
                continue
            if not state.is_signal_active(algorithm.time):
                continue

            target_value = target.percent * total_value
            if security.price <= 0 or target_value <= 0:
                continue
            quantity = int(target_value / security.price)
            if quantity <= 0:
                continue

            tag = state.order_tag("entry")
            ticket = algorithm.market_order(symbol, quantity, tag=tag)
            if ticket is not None:
                order_id = getattr(ticket, "order_id", getattr(ticket, "OrderId", None))
                if order_id is not None:
                    state.record_entry_order(order_id, algorithm.time)


class LongRvol9EmaFrameworkAlgorithm(QCAlgorithm):  # Main QuantConnect algorithm class.
    def initialize(self) -> None:  # QuantConnect calls initialize once at startup.
        self.set_start_date(2025, 1, 1)  # Original audit backtest start date.
        self.set_end_date(2026, 5, 23)  # Original audit backtest end date.
        self.set_cash(25000)  # Start the backtest with 25k in cash.
        self.set_brokerage_model(BrokerageName.CHARLES_SCHWAB, AccountType.MARGIN)
        self.settings.seed_initial_prices = True  # Seed prices during warmup so indicators can initialize more smoothly.

        spy = self.add_equity("SPY", Resolution.MINUTE)  # Anchor schedule rules to a U.S. market calendar.
        self._session_symbol = spy.symbol  # Store SPY's symbol for scheduling and benchmark configuration.
        self.set_benchmark(self._session_symbol)  # Use SPY as the benchmark.

        self.universe_settings.asynchronous = True  # Allow universe selection updates to happen asynchronously.
        self.universe_settings.resolution = Resolution.MINUTE  # Request minute data for selected symbols.
        self.universe_settings.fill_forward = False  # Avoid synthetic bars when no trades occur.
        self.universe_settings.extended_market_hours = False  # Use regular market hours only.
        self.add_universe_selection(LargeCapProfitableUniverseSelectionModel(max_universe_size=50))

        # --- Strategy parameters ---
        self._cash_buffer_percent = 0.10  # Keep at least 10% of portfolio value in idle cash.
        self.settings.free_portfolio_value_percentage = self._cash_buffer_percent
        self._target_position_percent = 0.09  # Target 9% per signal.
        self._flatten_buffer = timedelta(minutes=5)  # Flatten five minutes before the actual exchange close.
        self._fee_per_order = 1.0  # Kept at $1 to stay comparable with the audited export.
        self._slippage_percent = 0.0002  # 2 bps per-side slippage on top of the bid/ask touch.
        self._min_target_change = 0.005  # 0.5% minimum target change to avoid micro-rebalances.

        # Shared lifecycle registry: one source of truth for signal/position state.
        self._lifecycle = LifecycleRegistry(min_target_change=self._min_target_change)

        # --- Reality modeling ---
        self.set_security_initializer(self._configure_security)

        self._alpha = LongRvol9EmaAlphaModel(
            target_position_percent=self._target_position_percent,
            session_flatten_buffer=self._flatten_buffer,
            lifecycle_registry=self._lifecycle,
        )
        self.add_alpha(self._alpha)

        self.set_portfolio_construction(InsightWeightingPortfolioConstructionModel())
        self.add_risk_management(ProfitTargetAndMaxLossRiskManagementModel(0.02, 1000, self._lifecycle))
        self.set_execution(LifecycleExecutionModel(self._lifecycle, self._min_target_change))

        # Exchange-calendar-aware flatten.  BeforeMarketClose respects half-days and holidays.
        self.schedule.on(
            self.date_rules.every_day(self._session_symbol),
            self.time_rules.before_market_close(self._session_symbol, int(self._flatten_buffer.total_seconds() / 60)),
            self._flatten_end_of_day,
        )

        self.set_warm_up(timedelta(days=20), Resolution.MINUTE)  # Warm up with 20 calendar days of minute data.

    def _configure_security(self, security: Security) -> None:
        """Apply the documented fee and slippage models to every added security."""
        security.set_fee_model(FixedFeePerOrderModel(self._fee_per_order))
        security.set_slippage_model(PercentSlippageModel(self._slippage_percent))

    def _flatten_end_of_day(self) -> None:  # Scheduled helper that exits open positions.
        # Lock the alpha and lifecycle state first so no new entry orders can be created.
        self._alpha.lock_session(self)

        def _order_id(ticket):
            return getattr(ticket, "order_id", getattr(ticket, "OrderId", None))

        def _cancel(ticket, tag):
            fn = getattr(ticket, "cancel", getattr(ticket, "Cancel", None))
            if callable(fn):
                fn(tag)

        # Liquidate every holding with an auditable EOD tag.
        for security in [s for s in self.securities.values() if s.invested]:
            state = self._lifecycle.get(security.symbol.value)
            state.exit_reason = ExitReason.EOD
            tag = state.order_tag(ExitReason.EOD.value)
            ticket = self.market_order(security.symbol, -security.holdings.quantity, tag=tag)
            order_id = _order_id(ticket)
            if order_id is not None:
                state.record_exit_order(order_id, ExitReason.EOD, self.time)

        # Cancel any open entry orders that have not filled yet.
        open_orders_fn = getattr(self.transactions, "get_open_orders", getattr(self.transactions, "GetOpenOrders", None))
        if callable(open_orders_fn):
            for ticket in open_orders_fn():
                qty = getattr(ticket, "quantity", getattr(ticket, "Quantity", 0))
                if qty > 0:
                    _cancel(ticket, "Cancelled by EOD flatten")

        # Finalise lifecycle lock.
        self._lifecycle.lock_for_session_close()


class ProfitTargetAndMaxLossRiskManagementModel(RiskManagementModel):
    """Custom risk model that exits positions on a fixed profit target or a fixed dollar loss cap.

    It communicates the exit reason to the lifecycle registry so the execution
    model can tag the closing order correctly.
    """

    def __init__(
        self,
        profit_target_percent: float = 0.02,
        max_loss_dollars: float = 1000,
        lifecycle_registry: LifecycleRegistry | None = None,
    ) -> None:
        super().__init__()
        self._profit_target_percent = profit_target_percent
        self._max_loss_dollars = max_loss_dollars
        self._lifecycle = lifecycle_registry

    def manage_risk(self, algorithm: QCAlgorithm, targets: list[PortfolioTarget]) -> list[PortfolioTarget]:
        risk_adjusted_targets = []
        for security in algorithm.securities.values():
            if not security.invested:
                continue

            average_price = security.holdings.average_price
            if average_price <= 0:
                continue

            return_from_entry = (security.price - average_price) / average_price
            unrealized_profit = security.holdings.unrealized_profit

            if return_from_entry >= self._profit_target_percent:
                algorithm.insights.cancel([security.symbol])
                if self._lifecycle is not None:
                    state = self._lifecycle.get(security.symbol.value)
                    state.exit_reason = ExitReason.PROFIT_TARGET
                risk_adjusted_targets.append(PortfolioTarget(security.symbol, 0))
                continue

            if unrealized_profit <= -self._max_loss_dollars:
                algorithm.insights.cancel([security.symbol])
                if self._lifecycle is not None:
                    state = self._lifecycle.get(security.symbol.value)
                    state.exit_reason = ExitReason.MAX_LOSS
                risk_adjusted_targets.append(PortfolioTarget(security.symbol, 0))

        return risk_adjusted_targets


class LargeCapProfitableUniverseSelectionModel(FundamentalUniverseSelectionModel):
    """Universe model using fundamental filters."""

    def __init__(self, max_universe_size: int = 50) -> None:
        super().__init__(True, None)  # Enable fundamental data in the base universe model.
        self._max_universe_size = max_universe_size  # Store the max number of symbols to keep.

    def select(self, algorithm: QCAlgorithm, fundamental: list[Fundamental]) -> list[Symbol]:
        candidates = []
        for asset in fundamental:
            if not asset.has_fundamental_data:
                continue
            if asset.price < 30 or asset.price > 500:
                continue
            if asset.market_cap < 5_000_000_000:
                continue
            if not self._has_positive_quarterly_eps(asset):
                continue
            candidates.append(asset)

        liquid = sorted(candidates, key=lambda x: x.dollar_volume, reverse=True)
        return [asset.symbol for asset in liquid[: self._max_universe_size]]

    def _has_positive_quarterly_eps(self, asset: Fundamental) -> bool:
        eps_paths = [
            ("earning_reports", "basic_eps", "three_months"),
            ("earning_reports", "diluted_eps", "three_months"),
        ]
        for path in eps_paths:
            current = asset
            try:
                for part in path:
                    current = getattr(current, part)
                value = float(current)
            except Exception:
                continue
            if not math.isnan(value):
                return value > 0
        return False


class LongRvol9EmaAlphaModel(AlphaModel):
    """Framework alpha model that emits long signals with same-session expiry."""

    name = "LongRvol9EmaAlphaModel"

    def __init__(
        self,
        scan_time: time = time(9, 45),
        max_ranked_symbols: int = 10,
        rvol_lookback_days: int = 20,
        insight_horizon: timedelta = timedelta(hours=6),
        session_flatten_buffer: timedelta = timedelta(minutes=5),
        target_position_percent: float = 0.09,
        lifecycle_registry: LifecycleRegistry | None = None,
    ) -> None:
        super().__init__()
        self._scan_time = scan_time
        self._max_ranked_symbols = max_ranked_symbols
        self._rvol_lookback_days = rvol_lookback_days
        self._insight_horizon = insight_horizon
        self._session_flatten_buffer = session_flatten_buffer
        self._target_position_percent = target_position_percent
        self._lifecycle = lifecycle_registry
        self._states: dict[Symbol, SymbolState] = {}
        self._last_scan_date = None
        self._session_locked = False

    def update(self, algorithm: QCAlgorithm, data: Slice) -> list[Insight]:
        if algorithm.is_warming_up:
            return []

        today = algorithm.time.date()
        if self._last_scan_date != today:
            self._last_scan_date = today
            self._session_locked = False
            if self._lifecycle is not None:
                self._lifecycle.reset_for_session(today)
            for state in self._states.values():
                state.reset_for_session(today)

        if self._session_locked:
            return []

        insights = []

        for symbol, state in self._states.items():
            if symbol not in data.bars:
                continue

            bar = data.bars[symbol]
            state.update_intraday_volume(bar)

            if state.pending_breakout_high is None:
                continue

            ticker = symbol.value
            lifecycle = self._lifecycle.get(ticker) if self._lifecycle is not None else None
            already_invested = algorithm.securities[symbol].invested

            if state.trade_taken_today or already_invested:
                continue

            if lifecycle is not None and not lifecycle.can_emit_signal(algorithm.time):
                continue

            if bar.high > state.pending_breakout_high:
                close_time = self._get_insight_close_time(algorithm, symbol)
                if close_time is None or close_time <= algorithm.time:
                    state.pending_breakout_high = None
                    state.pending_timeframe = None
                    continue

                signal_id = f"{ticker}_{algorithm.time.strftime('%Y%m%d_%H%M%S')}"
                if lifecycle is not None:
                    lifecycle.record_signal(
                        signal_id=signal_id,
                        signal_time=algorithm.time,
                        signal_expiry=close_time,
                        intended_target=self._target_position_percent,
                    )

                state.trade_taken_today = True
                state.pending_breakout_high = None
                state.pending_timeframe = None

                tag = f"sig={signal_id}|tgt={self._target_position_percent}|sess={today}"
                insights.append(
                    Insight.price(
                        symbol,
                        close_time,
                        InsightDirection.UP,
                        weight=self._target_position_percent,
                        tag=tag,
                    )
                )

        if algorithm.time.time() >= self._scan_time:
            self._run_scan_if_needed(algorithm)

        return insights

    def on_securities_changed(self, algorithm: QCAlgorithm, changes: SecurityChanges) -> None:
        for security in changes.added_securities:
            symbol = security.symbol
            if symbol in self._states:
                continue

            state = SymbolState(
                algorithm=algorithm,
                symbol=symbol,
                rvol_lookback_days=self._rvol_lookback_days,
            )
            self._states[symbol] = state
            if self._lifecycle is not None:
                self._lifecycle.get(symbol.value)

        for security in changes.removed_securities:
            symbol = security.symbol
            algorithm.insights.cancel([symbol])
            state = self._states.pop(symbol, None)
            if self._lifecycle is not None:
                self._lifecycle._states.pop(symbol.value, None)
            if state is None:
                continue
            state.dispose()

    def _run_scan_if_needed(self, algorithm: QCAlgorithm) -> None:
        today = algorithm.time.date()
        already_scanned = any(state.scan_completed_today for state in self._states.values())
        if already_scanned:
            return

        ranked = []
        for state in self._states.values():
            state.scan_completed_today = True
            rvol = state.relative_volume()
            if rvol is None or rvol <= 1:
                continue
            if not state.has_positive_open_drive():
                continue
            ranked.append((rvol, state))

        ranked.sort(key=lambda x: x[0], reverse=True)
        top_symbols = {state.symbol for _, state in ranked[: self._max_ranked_symbols]}

        for state in self._states.values():
            state.selected_in_scan = state.symbol in top_symbols

    def lock_session(self, algorithm: QCAlgorithm) -> None:
        """Invalidate every active insight and block new entries until the next session."""
        self._session_locked = True
        if self._states:
            algorithm.insights.cancel(list(self._states.keys()))
        for state in self._states.values():
            state.lock_for_session_close()
        if self._lifecycle is not None:
            self._lifecycle.lock_for_session_close()

    def _get_insight_close_time(self, algorithm: QCAlgorithm, symbol: Symbol) -> datetime | None:
        """Return a same-session expiry strictly before the scheduled flatten."""
        security = algorithm.securities[symbol]
        market_close_time = security.exchange.hours.get_next_market_close(algorithm.time, extended_market_hours=False)
        session_exit_time = market_close_time - self._session_flatten_buffer
        max_horizon_close = algorithm.time + self._insight_horizon
        close_time = min(max_horizon_close, session_exit_time)
        if close_time <= algorithm.time:
            return None
        return close_time


class SymbolState:
    """Per-symbol state machine holding intraday indicators, flags, and bar history."""

    two_minute_support_bars = 7  # Require the last 7 two-minute closes to remain above the 2-minute 9 EMA.
    five_minute_support_bars = 3  # Require 3 five-minute closes above the 5-minute 9 EMA before backup entries.
    pullback_pierce_limit = 0.0015  # Allow the low to come within roughly 0.15% of the EMA.
    max_pullback_depth_from_ema = 0.0035  # Reject pullbacks that pierce too deeply below the EMA.

    def __init__(self, algorithm: QCAlgorithm, symbol: Symbol, rvol_lookback_days: int) -> None:
        self.algorithm = algorithm
        self.symbol = symbol
        self._rvol_lookback_days = rvol_lookback_days

        self.ema2 = ExponentialMovingAverage(9)
        self.ema5 = ExponentialMovingAverage(9)
        self._bars2: deque[BarSnapshot] = deque(maxlen=30)
        self._bars5: deque[BarSnapshot] = deque(maxlen=30)

        self._baseline_cumulative_volume = self._load_volume_profile()

        self._consolidator2 = TradeBarConsolidator(timedelta(minutes=2))
        self._consolidator2.data_consolidated += self._on_two_minute_bar
        self.algorithm.subscription_manager.add_consolidator(symbol, self._consolidator2)

        self._consolidator5 = TradeBarConsolidator(timedelta(minutes=5))
        self._consolidator5.data_consolidated += self._on_five_minute_bar
        self.algorithm.subscription_manager.add_consolidator(symbol, self._consolidator5)

        self.reset_for_session(self.algorithm.time.date())

    def dispose(self) -> None:
        self.algorithm.subscription_manager.remove_consolidator(self.symbol, self._consolidator2)
        self.algorithm.subscription_manager.remove_consolidator(self.symbol, self._consolidator5)

    def reset_for_session(self, session_date: date) -> None:
        self.session_date = session_date
        self.day_open = None
        self.cumulative_volume = 0
        self.scan_completed_today = False
        self.selected_in_scan = False
        self.trade_taken_today = False
        self.two_minute_invalidated = False
        self.five_minute_invalidated = False
        self.pending_breakout_high = None
        self.pending_timeframe = None
        self.first_two_minute_pullback_consumed = False
        self.first_five_minute_pullback_consumed = False
        self._bars2.clear()
        self._bars5.clear()
        self.ema2.reset()
        self.ema5.reset()

    def lock_for_session_close(self) -> None:
        self.selected_in_scan = False
        self.trade_taken_today = True
        self.pending_breakout_high = None
        self.pending_timeframe = None

    def update_intraday_volume(self, bar: TradeBar) -> None:
        if self.day_open is None:
            self.day_open = bar.open
        self.cumulative_volume += bar.volume

    def has_positive_open_drive(self) -> bool:
        security = self.algorithm.securities[self.symbol]
        if self.day_open is None:
            return False
        return security.price > self.day_open

    def relative_volume(self) -> float | None:
        minute_offset = self._session_minute_offset(self.algorithm.time)
        baseline = self._baseline_cumulative_volume.get(minute_offset)
        if baseline is None or baseline <= 0:
            return None
        return self.cumulative_volume / baseline

    def _load_volume_profile(self) -> dict[int, float]:
        """Build an average cumulative volume curve by minute offset.

        This is performed once when a symbol enters the universe.  The resulting
        dictionary is used point-in-time by relative_volume().
        """
        history = self.algorithm.history(self.symbol, timedelta(days=self._rvol_lookback_days * 3), Resolution.MINUTE)
        if history.empty:
            return {}

        daily_profiles = defaultdict(lambda: defaultdict(float))
        try:
            rows = history.loc[self.symbol]
        except Exception:
            rows = history

        for end_time, row in rows.iterrows():
            local_time = end_time.time()
            if local_time < time(9, 30) or local_time > time(16, 0):
                continue
            session = end_time.date()
            minute_offset = self._session_minute_offset(end_time)
            daily_profiles[session][minute_offset] += float(row["volume"])

        if not daily_profiles:
            return {}

        sessions = sorted(daily_profiles.keys())[-self._rvol_lookback_days :]
        cumulative_profiles = defaultdict(list)
        for session in sessions:
            running = 0.0
            for minute_offset in sorted(daily_profiles[session].keys()):
                running += daily_profiles[session][minute_offset]
                cumulative_profiles[minute_offset].append(running)

        averages = {}
        for minute_offset, samples in cumulative_profiles.items():
            if samples:
                averages[minute_offset] = sum(samples) / len(samples)
        return averages

    def _on_two_minute_bar(self, sender: object, bar: TradeBar) -> None:
        self.ema2.update(bar.end_time, bar.close)
        if not self.ema2.is_ready:
            return

        snapshot = BarSnapshot(
            end_time=bar.end_time,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            ema=float(self.ema2.current.value),
        )
        self._bars2.append(snapshot)

        if not self.selected_in_scan or self.trade_taken_today:
            return

        if snapshot.close < snapshot.ema:
            self.two_minute_invalidated = True
            if self.pending_timeframe == "2m":
                self.pending_breakout_high = None
                self.pending_timeframe = None
            return

        if self.two_minute_invalidated:
            return

        if snapshot.end_time.time() < time(9, 44):
            return

        if not self._support_held(self._bars2, self.two_minute_support_bars):
            return

        if self.first_two_minute_pullback_consumed:
            return

        if self._is_orderly_pullback(snapshot):
            self.pending_breakout_high = snapshot.high
            self.pending_timeframe = "2m"
            self.first_two_minute_pullback_consumed = True

    def _on_five_minute_bar(self, sender: object, bar: TradeBar) -> None:
        self.ema5.update(bar.end_time, bar.close)
        if not self.ema5.is_ready:
            return

        snapshot = BarSnapshot(
            end_time=bar.end_time,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            ema=float(self.ema5.current.value),
        )
        self._bars5.append(snapshot)

        if not self.selected_in_scan or self.trade_taken_today or not self.two_minute_invalidated:
            return

        if snapshot.close < snapshot.ema:
            self.five_minute_invalidated = True
            if self.pending_timeframe == "5m":
                self.pending_breakout_high = None
                self.pending_timeframe = None
            return

        if self.five_minute_invalidated:
            return

        if len(self._bars5) < self.five_minute_support_bars:
            return

        if not self._support_held(self._bars5, self.five_minute_support_bars):
            return

        if self.first_five_minute_pullback_consumed:
            return

        if self._is_orderly_pullback(snapshot):
            self.pending_breakout_high = snapshot.high
            self.pending_timeframe = "5m"
            self.first_five_minute_pullback_consumed = True

    def _support_held(self, bars: deque[BarSnapshot], count: int) -> bool:
        if len(bars) < count:
            return False
        recent = list(bars)[-count:]
        return all(bar.close > bar.ema for bar in recent)

    def _is_orderly_pullback(self, bar: BarSnapshot) -> bool:
        if bar.close <= bar.ema:
            return False

        lower_pierce = (bar.ema - bar.low) / bar.ema
        if lower_pierce < -self.pullback_pierce_limit:
            return False
        if lower_pierce > self.max_pullback_depth_from_ema:
            return False

        return bar.low <= bar.ema * (1 + self.pullback_pierce_limit)

    def _session_minute_offset(self, dt: datetime) -> int:
        return (dt.hour * 60 + dt.minute) - (9 * 60 + 30)
