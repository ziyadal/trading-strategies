class VwapPullbackEntryStrategy(QCAlgorithm):

    def initialize(self):
        self.set_start_date(2019, 1, 11)
        self.set_end_date(2026, 9, 1)
        self.set_cash(25000)

        self.set_brokerage_model(
            BrokerageName.CharlesSchwab,
            AccountType.CASH
        )

        self.universe_settings.resolution = Resolution.MINUTE
        self.add_universe(self.select)

        self.setups = {}
        self.trades = {}

    def on_data(self, data: Slice):
        if self.is_warming_up:
            return

        self._watch_for_vwap_pullback_entries(data)
        self._manage_open_trades(data)

    def _watch_for_vwap_pullback_entries(self, data):
        for symbol in list(self.setups):

            if not data.bars.contains_key(symbol):
                continue

            bar = data.bars[symbol]

            if self._vwap_pullback_entry(symbol, bar):
                self._enter(symbol, bar)

    def _vwap_pullback_entry(self, symbol, bar):
        """
        Returns True when the VWAP pullback entry conditions are satisfied.
        """

        setup = self.setups[symbol]

        vwap = setup["vwap"]

        # Example:
        # Price pulls back into VWAP but closes back above it.
        touched_vwap = bar.low <= vwap
        reclaimed_vwap = bar.close > vwap

        return touched_vwap and reclaimed_vwap