from AlgorithmImports import *

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import math
from statistics import median
from typing import Dict, List, Optional


class CuratedFundamentalUniverseSelectionModel(FundamentalUniverseSelectionModel):
    def __init__(self) -> None:
        super().__init__()
        self._candidate_symbols: List[Symbol] = []
        self._candidate_market_caps: Dict[Symbol, float] = {}
        self._last_selection_date: Optional[date] = None

    def get_candidates(self) -> List[Symbol]:
        return list(self._candidate_symbols)

    def get_market_caps(self) -> Dict[Symbol, float]:
        return dict(self._candidate_market_caps)

    def select(self, algorithm: QCAlgorithm, fundamental: List[Fundamental]) -> List[Symbol]:
        selected = []

        for stock in fundamental:
            market_cap = stock.market_cap
            price = stock.price
            net_income_ttm = stock.financial_statements.income_statement.net_income.twelve_months

            if not stock.has_fundamental_data:
                continue

            if stock.company_reference.country_id != "USA":
                continue

            if not self._is_finite(market_cap) or market_cap <= 5_000_000_000:
                continue

            if not self._is_finite(price) or price < 30 or price > 500:
                continue

            if not self._is_finite(net_income_ttm) or net_income_ttm <= 0:
                continue

            selected.append(stock)

        selected.sort(key=lambda stock: stock.market_cap, reverse=True)
        self._candidate_symbols = [stock.symbol for stock in selected]
        self._candidate_market_caps = {
            stock.symbol: float(stock.market_cap) for stock in selected
        }
        self._last_selection_date = algorithm.time.date()
        return []

    @staticmethod
    def _is_finite(value: object) -> bool:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return False
        return math.isfinite(numeric)


class LargeCapProfitableUsUniverseAlgorithm(QCAlgorithm):
    def initialize(self) -> None:
        self.set_start_date(2023, 1, 1)
        self.set_end_date(2026, 8, 25)
        self.set_time_zone(TimeZones.NEW_YORK)
        self.set_cash(25000)

        self.settings.free_portfolio_value_percentage = 0.05

        self.market_open_reference = self.add_equity("SPY", Resolution.DAILY).symbol

        self.max_rvol_candidates = 50
        self.rvol_lookback_sessions = 10

        self.curated_model = CuratedFundamentalUniverseSelectionModel()
        self.latest_rvol_by_symbol: Dict[Symbol, float] = {}
        self.rvol_qualified_symbols: List[Symbol] = []
        self.trade_levels_by_symbol: Dict[Symbol, TradeRiskLevels] = {}

        self.universe_settings.resolution = Resolution.MINUTE
        self.universe_settings.asynchronous = False
        self.universe_settings.minimum_time_in_universe = timedelta(0)

        self.set_brokerage_model(BrokerageName.CHARLES_SCHWAB, AccountType.CASH)

        self.set_portfolio_construction(FixedAllocationPortfolioConstructionModel(0.10, 0.05))
        self.set_execution(ImmediateExecutionModel())

        self.add_universe_selection(self.curated_model)

        self.add_universe_selection(
            ScheduledUniverseSelectionModel(
                self.date_rules.every_day(self.market_open_reference),
                self.time_rules.after_market_open(self.market_open_reference, 0),
                self._clear_rvol_universe,
            )
        )

        self.schedule.on(
            self.date_rules.every_day(self.market_open_reference),
            self.time_rules.after_market_open(self.market_open_reference, 15),
            self._refresh_rvol_candidates,
        )

        self.schedule.on(
            self.date_rules.every_day(self.market_open_reference),
            self.time_rules.after_market_open(self.market_open_reference, 120),
            self._stop_new_entries_for_day,
        )

        self.add_universe_selection(
            ScheduledUniverseSelectionModel(
                self.date_rules.every_day(self.market_open_reference),
                self.time_rules.after_market_open(self.market_open_reference, 16),
                self._select_cached_rvol_universe,
            )
        )

        self.schedule.on(
            self.date_rules.every_day(self.market_open_reference),
            self.time_rules.before_market_close(self.market_open_reference, 1),
            self._flatten_positions_end_of_day,
        )

        self.ema_support_alpha = TwoMinuteEmaSupportAlphaModel()
        self.add_alpha(self.ema_support_alpha)

    def _clear_rvol_universe(self, current_time: datetime) -> List[Symbol]:
        self.ema_support_alpha.set_trading_enabled(True)
        self.latest_rvol_by_symbol = {}
        self.rvol_qualified_symbols = []
        return []

    def _stop_new_entries_for_day(self) -> None:
        self.ema_support_alpha.set_trading_enabled(False)

    def _flatten_positions_end_of_day(self) -> None:
        self.ema_support_alpha.set_trading_enabled(False)
        self.latest_rvol_by_symbol = {}
        self.rvol_qualified_symbols = []

        symbols_to_cancel = [symbol for symbol in self.securities.keys()]
        self.insights.cancel(symbols_to_cancel)

        for security in self.securities.values():
            if not security.invested:
                continue

            trade_levels = self.trade_levels_by_symbol.get(security.symbol)
            if trade_levels is not None:
                trade_levels.exit_order_sent = True
            self.liquidate(security.symbol, tag="End-of-day liquidation")

    def _refresh_rvol_candidates(self) -> None:
        candidates = self.curated_model.get_candidates()
        market_caps = self.curated_model.get_market_caps()
        if not candidates:
            self.latest_rvol_by_symbol = {}
            self.rvol_qualified_symbols = []
            return

        candidates = sorted(
            candidates,
            key=lambda symbol: (-market_caps.get(symbol, 0.0), symbol.value),
        )[: self.max_rvol_candidates]

        session_opens = self._get_recent_session_opens(self.time, self.rvol_lookback_sessions)
        first_window_volumes = self._get_first_window_volumes(candidates, session_opens)

        qualified_symbols = []
        rvol_by_symbol = {}

        for symbol in candidates:
            volume_series = first_window_volumes.get(symbol, [])

            if (
                len(volume_series) != self.rvol_lookback_sessions
                or any(volume is None for volume in volume_series)
            ):
                continue

            today_open_window_volume = volume_series[-1]
            median_open_window_volume = median(volume_series[:-1])

            if not median_open_window_volume or median_open_window_volume <= 0:
                continue

            rvol = today_open_window_volume / median_open_window_volume
            if rvol < 1.5:
                continue

            rvol_by_symbol[symbol] = rvol
            qualified_symbols.append(symbol)

        qualified_symbols.sort(
            key=lambda symbol: (
                -rvol_by_symbol[symbol],
                -market_caps.get(symbol, 0.0),
                symbol.value,
            )
        )

        self.latest_rvol_by_symbol = rvol_by_symbol
        self.rvol_qualified_symbols = qualified_symbols

    def _select_cached_rvol_universe(self, current_time: datetime) -> List[Symbol]:
        return self.rvol_qualified_symbols

    def _get_recent_session_opens(self, current_time: datetime, session_count: int) -> List[datetime]:
        exchange_hours = self.securities[self.market_open_reference].exchange.hours
        session_day = current_time
        session_opens = []

        for _ in range(session_count):
            session_midnight = session_day.replace(hour=0, minute=0, second=0, microsecond=0)
            session_opens.append(
                exchange_hours.get_next_market_open(
                    session_midnight,
                    extended_market_hours=False,
                )
            )
            session_day = exchange_hours.get_previous_trading_day(session_day)

        session_opens.reverse()
        return session_opens

    def _get_first_window_volumes(
        self,
        symbols: List[Symbol],
        session_opens: List[datetime],
    ) -> Dict[Symbol, List[Optional[float]]]:
        volume_series_by_symbol = {symbol: [None] * len(session_opens) for symbol in symbols}
        if not symbols or not session_opens:
            return volume_series_by_symbol

        session_volumes = {symbol: [0.0] * len(session_opens) for symbol in symbols}
        session_bar_counts = {symbol: [0] * len(session_opens) for symbol in symbols}

        for i, session_open in enumerate(session_opens):
            window_end = session_open + timedelta(minutes=15)
            history = self.history[TradeBar](
                symbols, session_open, window_end, Resolution.MINUTE
            )
            for trade_bars in history:
                for kvp in trade_bars:
                    symbol = kvp.key
                    trade_bar = kvp.value

                    if session_open < trade_bar.end_time <= window_end:
                        session_volumes[symbol][i] += float(trade_bar.volume)
                        session_bar_counts[symbol][i] += 1

        for symbol in symbols:
            volume_series_by_symbol[symbol] = [
                session_volumes[symbol][i] if session_bar_counts[symbol][i] == 15 else None
                for i in range(len(session_opens))
            ]

        return volume_series_by_symbol

    def on_data(self, slice: Slice) -> None:
        for symbol, trade_levels in list(self.trade_levels_by_symbol.items()):
            security = self.portfolio[symbol]

            if not security.invested:
                self.trade_levels_by_symbol.pop(symbol, None)
                continue

            if trade_levels.exit_order_sent:
                continue

            bar = slice.bars.get_value(symbol)
            if bar is None:
                continue

            bar_high = float(bar.high)
            trade_levels.highest_price_since_entry = max(
                trade_levels.highest_price_since_entry,
                bar_high,
            )
            stop_price = trade_levels.active_stop_price
            if float(bar.low) <= stop_price:
                self._liquidate_with_cancelled_insight(
                    symbol,
                    f"ATR stop hit at {stop_price:.2f}",
                )
                trade_levels.exit_order_sent = True

    def on_order_event(self, order_event: OrderEvent) -> None:
        if order_event.status != OrderStatus.FILLED:
            return

        symbol = order_event.symbol
        holding = self.portfolio[symbol]

        if order_event.fill_quantity > 0 and holding.is_long:
            atr_value = self.ema_support_alpha.get_atr_value(symbol)
            if atr_value is None or atr_value <= 0:
                self._liquidate_with_cancelled_insight(
                    symbol,
                    "ATR unavailable on entry",
                )
                return

            stop_distance = 2.5 * atr_value
            entry_price = float(order_event.fill_price)
            initial_stop_price = entry_price - stop_distance

            self.trade_levels_by_symbol[symbol] = TradeRiskLevels(
                initial_stop_price=float(initial_stop_price),
                active_stop_price=float(initial_stop_price),
                highest_price_since_entry=entry_price,
            )
            return

        if order_event.fill_quantity < 0 and not holding.invested:
            self.trade_levels_by_symbol.pop(symbol, None)

    def _liquidate_with_cancelled_insight(
        self,
        symbol: Symbol,
        tag: str,
    ) -> None:
        self.insights.cancel([symbol])
        self.liquidate(symbol, tag=tag)

    def on_securities_changed(self, changes: SecurityChanges) -> None:
        pass


@dataclass
class TradeRiskLevels:
    initial_stop_price: float
    active_stop_price: float
    highest_price_since_entry: float
    exit_order_sent: bool = False


class FixedAllocationPortfolioConstructionModel(PortfolioConstructionModel):
    def __init__(self, target_weight: float, cash_buffer_pct: float) -> None:
        super().__init__()
        self.target_weight = target_weight
        self.cash_buffer_pct = cash_buffer_pct
        self.max_positions = max(1, int((1.0 - cash_buffer_pct) / target_weight))
        self._entered_today: Optional[date] = None

    def create_targets(self, algorithm: QCAlgorithm, insights: List[Insight]) -> List[PortfolioTarget]:
        targets = []

        flat_symbols = set()
        emitted_up = {}
        for insight in insights or []:
            if insight.direction == InsightDirection.FLAT:
                flat_symbols.add(insight.symbol)
            elif insight.direction == InsightDirection.UP:
                emitted_up[insight.symbol] = insight

        exit_symbols = set(flat_symbols)

        active_up = {}
        for insight in algorithm.insights.get_active_insights(algorithm.utc_time):
            if insight.direction == InsightDirection.UP:
                active_up[insight.symbol] = insight
            elif insight.direction == InsightDirection.FLAT:
                exit_symbols.add(insight.symbol)
        active_up.update(emitted_up)

        held_long = set()
        for symbol in list(algorithm.portfolio.keys()):
            if algorithm.portfolio[symbol].is_long:
                held_long.add(symbol)
                if symbol not in active_up:
                    exit_symbols.add(symbol)

        first_enter_batch = self._entered_today != algorithm.time.date()

        ranked = sorted(
            active_up.keys(),
            key=lambda symbol: (
                0 if symbol in held_long else 1,
                active_up[symbol].generated_time_utc,
            ),
        )
        ranked = [symbol for symbol in ranked if symbol not in exit_symbols]

        held_kept = [symbol for symbol in ranked if symbol in held_long][: self.max_positions]

        to_enter = []
        if first_enter_batch:
            free_slots = max(0, self.max_positions - len(held_kept))
            to_enter = [symbol for symbol in ranked if symbol not in held_long][:free_slots]
            if to_enter:
                self._entered_today = algorithm.time.date()

        for symbol in held_kept + to_enter:
            if not algorithm.securities[symbol].has_data:
                continue
            targets.append(
                PortfolioTarget.Percent(
                    algorithm,
                    symbol,
                    self.target_weight,
                    tag=f"Target {self.target_weight:.0%} allocation",
                )
            )

        for symbol in exit_symbols:
            if algorithm.portfolio[symbol].quantity != 0:
                targets.append(PortfolioTarget(symbol, 0, tag="Flat insight"))
        return targets


class TwoMinuteEmaSupportAlphaModel(AlphaModel):
    def __init__(self, ema_period: int = 9, touch_threshold_pct: float = 0.005) -> None:
        self.ema_period = ema_period
        self.touch_threshold_pct = touch_threshold_pct
        self.signal_duration = timedelta(hours=8)
        self.flat_signal_duration = timedelta(minutes=5)
        self.warmup_history_minutes = max(200, ema_period * 30)
        self.symbol_data_by_symbol: Dict[Symbol, TwoMinuteEmaSupportSymbolData] = {}
        self.trading_enabled = False

    def update(self, algorithm: QCAlgorithm, data: Slice) -> List[Insight]:
        insights = []

        for symbol_data in self.symbol_data_by_symbol.values():
            if symbol_data.pending_insights:
                insights.extend(symbol_data.pending_insights)
                symbol_data.pending_insights = []

        return insights

    def get_atr_value(self, symbol: Symbol) -> Optional[float]:
        symbol_data = self.symbol_data_by_symbol.get(symbol)
        if symbol_data is None or not symbol_data.atr.is_ready:
            return None
        return float(symbol_data.atr.current.value)

    def set_trading_enabled(self, enabled: bool) -> None:
        self.trading_enabled = enabled
        for symbol_data in self.symbol_data_by_symbol.values():
            symbol_data.trading_enabled = enabled

    def on_securities_changed(self, algorithm: QCAlgorithm, changes: SecurityChanges) -> None:
        for security in changes.removed_securities:
            symbol_data = self.symbol_data_by_symbol.pop(security.symbol, None)
            if symbol_data is None:
                continue

            algorithm.subscription_manager.remove_consolidator(security.symbol, symbol_data.consolidator)

        added_symbols = []
        for security in changes.added_securities:
            if security.symbol.value == "SPY":
                continue
            if security.symbol in self.symbol_data_by_symbol:
                continue

            symbol_data = TwoMinuteEmaSupportSymbolData(
                algorithm,
                security.symbol,
                self.ema_period,
                self.touch_threshold_pct,
                self.signal_duration,
                self.flat_signal_duration,
            )
            self.symbol_data_by_symbol[security.symbol] = symbol_data
            symbol_data.trading_enabled = self.trading_enabled
            added_symbols.append(security.symbol)

        if not added_symbols:
            return

        for symbol in added_symbols:
            self.symbol_data_by_symbol[symbol].is_history_warming_up = True

        history = algorithm.history[TradeBar](
            added_symbols,
            self.warmup_history_minutes,
            Resolution.MINUTE,
        )
        for trade_bars in history:
            for kvp in trade_bars:
                symbol_data = self.symbol_data_by_symbol.get(kvp.key)
                if symbol_data is not None:
                    symbol_data.consolidator.update(kvp.value)

        for symbol in added_symbols:
            symbol_data = self.symbol_data_by_symbol[symbol]
            symbol_data.is_history_warming_up = False
            algorithm.subscription_manager.add_consolidator(symbol, symbol_data.consolidator)
            symbol_data.arm_pullback_entry_if_valid(algorithm)


class TwoMinuteEmaSupportSymbolData:
    def __init__(
        self,
        algorithm: QCAlgorithm,
        symbol: Symbol,
        ema_period: int,
        touch_threshold_pct: float,
        signal_duration: timedelta,
        flat_signal_duration: timedelta,
    ) -> None:
        self.algorithm = algorithm
        self.symbol = symbol
        self.touch_threshold_pct = touch_threshold_pct
        self.signal_duration = signal_duration
        self.flat_signal_duration = flat_signal_duration
        self.ema = ExponentialMovingAverage(ema_period)
        self.atr = AverageTrueRange(14, MovingAverageType.WILDERS)
        self.consolidator = TradeBarConsolidator(
            timedelta(minutes=2),
            start_time=timedelta(hours=9, minutes=30),
        )
        self.consolidator.data_consolidated += self._on_two_minute_bar

        self.pending_insights: List[Insight] = []
        self.current_session_date: Optional[date] = None
        self.awaiting_pullback_entry = False
        self.setup_invalidated_today = False
        self.entry_signal_emitted_today = False
        self.is_history_warming_up = False
        self.trading_enabled = True

    def arm_pullback_entry_if_valid(self, algorithm: QCAlgorithm) -> None:
        if not self.trading_enabled or not self.ema.is_ready or self.setup_invalidated_today:
            return
        if self.awaiting_pullback_entry or self.entry_signal_emitted_today:
            return

        self.awaiting_pullback_entry = True

    def _on_two_minute_bar(self, sender: object, bar: TradeBar) -> None:
        session_date = bar.end_time.date()
        if self.current_session_date != session_date:
            self._reset_for_new_session(session_date)

        self.atr.update(bar)
        self.ema.update(bar.end_time, bar.close)

        if not self.ema.is_ready:
            return

        if self.is_history_warming_up:
            return

        if bar.close < self.ema.current.value:
            self.awaiting_pullback_entry = False

            if not self.setup_invalidated_today:
                self.setup_invalidated_today = True
                if self.entry_signal_emitted_today:
                    self.pending_insights.append(
                        Insight.price(self.symbol, self.flat_signal_duration, InsightDirection.FLAT)
                    )

            return

        if not self.trading_enabled:
            return

        self.arm_pullback_entry_if_valid(self.algorithm)

        if not self.awaiting_pullback_entry or self.entry_signal_emitted_today:
            return

        if self._is_pullback_touch(bar):
            self.pending_insights.append(
                Insight.price(self.symbol, self.signal_duration, InsightDirection.UP)
            )
            self.entry_signal_emitted_today = True
            self.awaiting_pullback_entry = False

    def _is_pullback_touch(self, bar: TradeBar) -> bool:
        ema_value = self.ema.current.value
        return bar.low <= ema_value * (1 + self.touch_threshold_pct)

    def _reset_for_new_session(self, session_date: date) -> None:
        self.current_session_date = session_date
        self.awaiting_pullback_entry = False
        self.setup_invalidated_today = False
        self.entry_signal_emitted_today = False
