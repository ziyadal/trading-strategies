  High-Level Structure

  - LongRvol9EmaFrameworkAlgorithm sets the backtest window, cash, benchmark, minute-resolution universe settings, and all
    framework models: universe selection, alpha, portfolio construction, risk, and execution
    long_rvol_9ema_framework_annotated.py:21.
  - LargeCapProfitableUniverseSelectionModel picks up to 50 stocks with fundamentals, price between $30 and $500, market cap
    above $5B, positive recent quarterly EPS, then keeps the most liquid names by dollar volume
    long_rvol_9ema_framework_annotated.py:88.
  - LongRvol9EmaAlphaModel watches those symbols intraday, ranks them by RVOL after 9:45, and emits long signals when a
    breakout condition is met long_rvol_9ema_framework_annotated.py:127.
  - SymbolState holds per-symbol intraday state: RVOL baseline, 2-minute and 5-minute EMAs, recent bars, and all daily flags
    long_rvol_9ema_framework_annotated.py:232.
  - ProfitTargetAndMaxLossRiskManagementModel handles exits at +2% profit or -$1,000 unrealized loss
    long_rvol_9ema_framework_annotated.py:60.

  How a Trade Happens

  1. The universe model builds a candidate list of large, profitable, liquid stocks.
  2. When a symbol enters the universe, the alpha model creates a SymbolState, loads roughly 20 trading days of minute volume
     history, and attaches 2-minute and 5-minute consolidators long_rvol_9ema_framework_annotated.py:189,
     long_rvol_9ema_framework_annotated.py:240.
  3. Every minute, the alpha model updates cumulative intraday volume for each symbol
     long_rvol_9ema_framework_annotated.py:147.

  4. At or after 9:45, it runs a one-time daily scan. A stock is eligible only if:
      - RVOL is above 1 (how are they calculating RVOL?)
      - price is above the day’s open
      - it ranks in the top 10 RVOL names that day
        See long_rvol_9ema_framework_annotated.py:209.

  5. Separately, the 2-minute consolidator looks for the main setup:
      - last 7 two-minute closes stayed above the 2-minute 9 EMA
      - the current bar is the first orderly pullback into the EMA area --- refine
      - if valid, the bar’s high becomes the pending breakout trigger
        See long_rvol_9ema_framework_annotated.py:338.

  6. If the 2-minute setup breaks down by closing under the EMA, it is invalidated for the day. Then a backup 5-minute version
     can take over, using 3 closes above the 5-minute 9 EMA and the same orderly-pullback logic
     long_rvol_9ema_framework_annotated.py:381.

  7. The actual entry happens on a later minute bar when bar.high > pending_breakout_high. At that moment the model emits
     Insight.price(..., InsightDirection.UP, weight=0.09) long_rvol_9ema_framework_annotated.py:166.

  How Execution Works

  - Portfolio construction is InsightWeightingPortfolioConstructionModel(), so that weight=0.09 becomes a target allocation of
    about 9% of portfolio value per trade long_rvol_9ema_framework_annotated.py:43.
  - Execution is ImmediateExecutionModel(), so the framework submits orders right away to move holdings to that target
    long_rvol_9ema_framework_annotated.py:45.
  - The algorithm also reserves 10% cash via free_portfolio_value_percentage, so it is intentionally not fully invested
    long_rvol_9ema_framework_annotated.py:36.

  How Trades Exit

  - No bracket order is placed at entry.
  - Exits are generated later by the risk model:
      - flatten if gain from average entry reaches 2%
      - flatten if unrealized PnL falls to -1000
        See long_rvol_9ema_framework_annotated.py:65.
  - All remaining positions are force-closed at 15:55 every trading day, so this is explicitly intraday-only right now
    long_rvol_9ema_framework_annotated.py:47.

  Behavioral Notes

  - Long-only. There is no short logic.
  - One trade per symbol per day because trade_taken_today is set after the first entry
    long_rvol_9ema_framework_annotated.py:172.
  - The EMAs are reset each session, so the setup is based on intraday structure, not multi-day EMA continuity
    long_rvol_9ema_framework_annotated.py:266.
#



Algorithm summary
1. Stock preparation
   When a stock enters the universe, the algorithm:
   - Creates a SymbolState to store its data and setup status.
   - Loads approximately 20 trading days of minute-volume history.
   - Builds 2-minute and 5-minute candles from incoming 1-minute data.
2. RVOL scan at 9:45 ET
   It calculates:
   Today’s cumulative volume from 9:30–9:45 ÷ average volume for the same period over the previous 20 sessions.
   The formula can work at any time, but it uses 15 minutes here because the scan runs once at 9:45. The historical baseline is loaded when the stock’s state is created and is not refreshed daily.
3. First orderly pullback
   The algorithm looks for the first qualifying completed 2- or 5-minute candle:
   - Recent candles must have closed above the 9 EMA.
   - Its low must enter the EMA zone: approximately 0.35% below to 0.15% above the EMA.
   - It must close above the EMA.
   - Its entire high-to-low range, including wicks, must be below 1.25%.
   A messy candle that fails these rules does not use up the setup. However, a 2-minute close below its EMA cancels the 2-minute setup for that day.
4. Breakout and buy signal
   The qualifying pullback candle’s high is saved as pending_breakout_high.
   The algorithm then watches later 1-minute candles:
   - A candle’s high must be strictly above the saved high.
   - Merely touching it is insufficient.
   - The 1-minute candle does not need to close above it.
   - It can be any later candle—not only the immediately following one—while the setup remains valid.
   Minute data contains OHLCV summaries, not individual order-book activity. An unfilled quote above the level does not trigger the signal.
5. Order and trade management
   Once the breakout occurs, the algorithm emits a long signal and submits an order. The signal, order, and actual fill are separate, so slippage may cause a different fill price.
   The long insight remains valid for up to six hours, allowing the stop-loss, any configured take-profit, or the end-of-day closing rule to manage the exit.
Main concern: The range filter is sensible for avoiding very volatile candles, but a fixed 1.25% limit may be too restrictive across different stocks and timeframes. It should be backtested against adaptive alternatives such as ATR or recent average candle range.


