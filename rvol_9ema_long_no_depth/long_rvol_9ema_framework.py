from AlgorithmImports import *

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import time, timedelta
import math


@dataclass
class BarSnapshot:
    end_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    ema: float


class LongRvol9EmaFrameworkAlgorithm(QCAlgorithm):
    def initialize(self) -> None:
        self.set_start_date(2025, 1, 1)
        self.set_end_date(2026, 8, 19)
        self.set_cash(25000)
        self.set_brokerage_model(BrokerageName.CHARLES_SCHWAB, AccountType.MARGIN)
        self.settings.seed_initial_prices = True

        spy = self.add_equity("SPY", Resolution.MINUTE)
        self._session_symbol = spy.symbol
        self.set_benchmark(self._session_symbol)

        self.universe_settings.asynchronous = True
        self.universe_settings.resolution = Resolution.MINUTE
        self.universe_settings.fill_forward = False
        self.universe_settings.extended_market_hours = False
        self.add_universe_selection(LargeCapProfitableUniverseSelectionModel(max_universe_size=50))

        self._cash_buffer_percent = 0.10
        self.settings.free_portfolio_value_percentage = self._cash_buffer_percent
        self._target_position_percent = 0.09

        self._alpha = LongRvol9EmaAlphaModel(target_position_percent=self._target_position_percent)
        self.add_alpha(self._alpha)

        self.set_portfolio_construction(InsightWeightingPortfolioConstructionModel())
        self.add_risk_management(ProfitTargetAndMaxLossRiskManagementModel(0.02, 1000))
        self.set_execution(ImmediateExecutionModel())


        self.schedule.on(
            self.date_rules.every_day(self._session_symbol),
            self.time_rules.at(15, 55),
            self._flatten_end_of_day
        )

        self.set_warm_up(timedelta(days=20), Resolution.MINUTE)

    def _flatten_end_of_day(self) -> None:
        self.liquidate()


class ProfitTargetAndMaxLossRiskManagementModel(RiskManagementModel):
    def __init__(self, profit_target_percent: float = 0.02, max_loss_dollars: float = 1000) -> None:
        self._profit_target_percent = profit_target_percent
        self._max_loss_dollars = max_loss_dollars

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
                risk_adjusted_targets.append(PortfolioTarget(security.symbol, 0))
                continue

            if unrealized_profit <= -self._max_loss_dollars:
                risk_adjusted_targets.append(PortfolioTarget(security.symbol, 0))

        return risk_adjusted_targets


class LargeCapProfitableUniverseSelectionModel(FundamentalUniverseSelectionModel):
    def __init__(self, max_universe_size: int = 50) -> None:
        super().__init__(True, None)
        self._max_universe_size = max_universe_size

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
    name = "LongRvol9EmaAlphaModel"

    def __init__(
        self,
        scan_time: time = time(9, 45),
        max_ranked_symbols: int = 10,
        rvol_lookback_days: int = 20,
        insight_horizon: timedelta = timedelta(hours=6),
        target_position_percent: float = 0.09,
    ) -> None:
        super().__init__()
        self._scan_time = scan_time
        self._max_ranked_symbols = max_ranked_symbols
        self._rvol_lookback_days = rvol_lookback_days
        self._insight_horizon = insight_horizon
        self._target_position_percent = target_position_percent
        self._states: dict[Symbol, SymbolState] = {}
        self._last_scan_date = None

    def update(self, algorithm: QCAlgorithm, data: Slice) -> list[Insight]:
        if algorithm.is_warming_up:
            return []

        today = algorithm.time.date()
        if self._last_scan_date != today:
            self._last_scan_date = today
            for state in self._states.values():
                state.reset_for_session(today)

        insights = []

        for symbol, state in self._states.items():
            if symbol not in data.bars:
                continue

            bar = data.bars[symbol]
            state.update_intraday_volume(bar)

            if state.pending_breakout_high is not None:
                if (
                    not state.trade_taken_today
                    and algorithm.securities[symbol].invested is False
                    and bar.high > state.pending_breakout_high
                ):
                    state.trade_taken_today = True
                    state.pending_breakout_high = None
                    state.pending_timeframe = None
                    insights.append(
                        Insight.price(
                            symbol,
                            self._insight_horizon,
                            InsightDirection.UP,
                            weight=self._target_position_percent,
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

        for security in changes.removed_securities:
            symbol = security.symbol
            state = self._states.pop(symbol, None)
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


class SymbolState:

    two_minute_support_bars = 7
    five_minute_support_bars = 3
    pullback_pierce_limit = 0.0015
    max_pullback_depth_from_ema = 0.0035

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
