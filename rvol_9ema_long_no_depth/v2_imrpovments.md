Additional filters:

("financial_statements", "income_statement", "normalized_income", "twelve_months"),  # Fallback normalized income path to account for one-time items fines, asset liquidation. 
            ("earning_reports", "basic_eps", "twelve_months"),  # Last-resort EPS proxy when net income fields are unavailable.


Adequate liquidity and a tight bid/ask spread.

### Weekly Pivot Levels

Calculate and display weekly pivots using the previous completed week’s high, low, and close:

- `P = (High + Low + Close) / 3`
- `R1 = (2 × P) − Low`
- `S1 = (2 × P) − High`
- `R2 = P + (High − Low)`
- `S2 = P − (High − Low)`

Also mark the previous week’s high and low.

Use these levels as support, resistance, and profit-target areas. Avoid entering directly below an unbroken weekly resistance level. Prefer longs above the weekly pivot or after price reclaims and successfully retests it.

How close to 9 ema does it need to go? 1.4
Do not enter if the next weekly pivot, previous-week high, or other major resistance is too close.

Risk managment: 

Place the stop below the pullback swing low or supporting weekly-pivot zone.

Use the next weekly pivot, previous-week high, or another clearly identified resistance level as the primary target.

Do not average down (if trade goes agianst you buy more)


("financial_statements", "income_statement", "net_income", "twelve_months"),  # Preferred TTM net income path. trailing-12-month net income.


currently no riusk maangment system.
 
oredrly pull back It means the distance between the candle’s highest and lowest price, including the wicks, must be less than 1.25% of the stock’s price.

Yes. In this setup, the RVOL scan happens once at 9:45 AM.
It compares:
Today’s total volume from 9:30–9:45
÷
Average total volume from 9:30–9:45 over the previous 20 trading days

For example:
- Today’s opening volume: 1,000,000 shares
- Historical average: 500,000 shares
- RVOL: 2.0
The formula is not permanently limited to 15 minutes. It uses 15 minutes only because the scan runs at 9:45. If the scan ran at 10:00, it would compare the first 30 minutes instead.


so your basically saying if the 'confirmation' candle has to be a certain range for it to be the trade indicator?


Main issues:
- A 6-hour signal can survive the 3:55 PM liquidation and reopen the position the next day.
- The 20-day RVOL baseline becomes outdated because it is not refreshed daily.
- Restarting the algorithm erases its setup history and may cause duplicate trades.
- Stop-loss and profit exits are soft, minute-based market exits—not guaranteed protective orders.
- Brokerage fees, slippage, leverage, and deployment settings are not fixed.
- Breakout execution occurs after a completed 1-minute candle, not precisely when price crosses the level.
- There are no reproducible backtest results, monitoring, reconciliation, alerts, or kill switch.


  If you want, I can also tell you whether 0.35% is a sensible threshold for this strategy or too tight.

self.notify.email/sms/telegram/web/sftp/ftp(...)

 on_brokerage_message, on_brokerage_disconnect/reconnect | Live broker operational signals | Schwab/API issues, reconnects, broker errors | Live only |


 dashbaord? 