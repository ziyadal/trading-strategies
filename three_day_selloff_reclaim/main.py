# region imports
from AlgorithmImports import *
# endregion
class LiquidUniverseSelection(QCAlgorithm):
    
    def initialize(self):
        self.set_start_date(2019, 1, 11)
        self.set_end_date(2026, 9, 1)
        self.set_cash(25000)
        # Charles Schwab model with a cash account: Schwab fees and no leverage
        self.set_brokerage_model(BrokerageName.CharlesSchwab, AccountType.CASH)
        self.settings.daily_precise_end_time = False
        self.universe_settings.resolution = Resolution.MINUTE
        self.add_universe(self.select)
        self.setups = {} # active pullback setups awaiting a 5-minute breakout
        self.trades = {} # open trades with stop/target/time-exit




#this is defining seleciton criteria for the universe selection.
# It runs before the open on each universe update and selects US equities with:
# - market cap above $5B
# - price between $30 and $500
# - positive trailing-12-month net income
    def select(self, fundamental):
        filtered = [
            x for x in fundamental
            if x.market_cap > 5e9
            and 30 <= x.price <= 500
            and x.financial_statements.income_statement.net_income.twelve_months > 0
        ]
        if not filtered:
            return []
        history = self.history(TradeBar, [x.symbol for x in filtered], 30, Resolution.DAILY) # download daily for passed stocks
        candidates = []
        for x in filtered:
            if x.symbol in self.trades:
                continue # only one trade per selloff event
            setup = self._pullback_setup(history, x.symbol)
            if setup is not None:
                candidates.append((x, setup))
        selected = sorted(candidates, key=lambda c: c[0].dollar_volume, reverse=True)[:8]
        self.setups = {x.symbol: setup for x, setup in selected}
        return [x.symbol for x, setup in selected]

    def _pullback_setup(self, history, symbol):
        # Returns setup data if the stock fell >= 2 ATR(14) over the last 3 sessions, else None
        if symbol not in history.index.get_level_values(0):
            return None
        bars = history.loc[symbol]
        if len(bars) < 15:
            return None
        high, low, close = bars["high"], bars["low"], bars["close"]
        prev_close = close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.iloc[-14:].mean()
        if close.iloc[-4] - close.iloc[-1] < 2 * atr:
            return None
        selloff = bars.iloc[-3:]
        watch_days = list(self.trading_calendar.get_trading_days(self.time, self.time + timedelta(days=7)))
        return {
            "day3_high": selloff["high"].iloc[-1],   # high of the final selloff day
            "selloff_low": selloff["low"].min(),     # lowest low of the 3-day selloff
            "atr": atr,
            "watch_until": watch_days[2],            # selection day + next 2 trading days
            "candle": None,                          # in-progress 5-minute candle
        }

    def on_data(self, data: Slice) -> None:
        # Do not trade while the algorithm is warming up.
        if self.is_warming_up:
            return
        self._watch_for_entries(data)
        self._manage_open_trades(data)

    def _watch_for_entries(self, data):
        # Build 5-minute candles from the 1-minute stream; on the first one that closes
        # above Day 3's high, buy at the next 1-minute bar (the first bar of the new candle)
        for symbol in list(self.setups):
            setup = self.setups[symbol]
            # expire once the watch window day has fully passed (.Date is the .NET midnight property)
            if self.time >= setup["watch_until"].Date + timedelta(days=1):
                del self.setups[symbol]
                continue
            if not data.bars.contains_key(symbol):
                continue
            bar = data.bars[symbol]
            bucket = bar.time.replace(minute=(bar.time.minute // 5) * 5, second=0, microsecond=0)
            candle = setup["candle"]
            if candle is not None and candle["bucket"] != bucket:
                if candle["close"] > setup["day3_high"]:
                    self._enter(symbol, bar)
                    continue
                setup["candle"] = None
            if setup["candle"] is None:
                setup["candle"] = {"bucket": bucket, "close": bar.close}
            else:
                setup["candle"]["close"] = bar.close

    def _enter(self, symbol, bar):
        setup = self.setups[symbol]
        quantity = self.calculate_order_quantity(symbol, 0.18)
        entry = bar.open
        stop = setup["selloff_low"] - 0.1 * setup["atr"]
        r = entry - stop
        # Cap total exposure at 90% of the portfolio
        room = 0.9 * self.portfolio.total_portfolio_value - self.portfolio.total_holdings_value
        quantity = min(quantity, int(room / entry))
        if quantity <= 0 or r <= 0:
            return
        self.market_order(symbol, quantity)
        exit_days = list(self.trading_calendar.get_trading_days(self.time, self.time + timedelta(days=10)))
        self.trades[symbol] = {"stop": stop, "target": entry + 2 * r, "exit_after": exit_days[4]}
        del self.setups[symbol]

    def _manage_open_trades(self, data):
        # Exit on stop, 2R target, or after 5 trading sessions
        for symbol, trade in list(self.trades.items()):
            if not data.bars.contains_key(symbol):
                continue
            bar = data.bars[symbol]
            if bar.low <= trade["stop"] or bar.high >= trade["target"] or self.time >= trade["exit_after"].Date + timedelta(days=1):
                self.liquidate(symbol)
                del self.trades[symbol]