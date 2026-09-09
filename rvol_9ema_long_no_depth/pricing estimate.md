# QuantConnect Pricing Estimate

Date: August 21, 2026

## Verified Monthly Node Pricing

Backtesting nodes:

- `B2-8` (`2 CPU`, `8 GB RAM`): `$14/month`
- `B4-12` (`4 CPU`, `12 GB RAM`): `$48/month`
- `B8-16` (`8 CPU`, `16 GB RAM`): `$96/month`
- `B4-16-GPU` (`4 CPU`, `16 GB RAM`): `$400/month`
- `B24-128` (`24 CPU`, `128 GB RAM`): `$768/month`

Research nodes:

- `R1-4` (`1 CPU`, `4 GB RAM`): `$12/month`
- `R2-8` (`2 CPU`, `8 GB RAM`): `$24/month`
- `R4-12` (`4 CPU`, `12 GB RAM`): `$48/month`
- `R8-16` (`8 CPU`, `16 GB RAM`): `$96/month`
- `R4-16-GPU` (`4 CPU`, `16 GB RAM`): `$400/month`
- `R24-128-GPU` (`24 CPU`, `128 GB RAM`): `$1,000/month`
- `R64-256` (`64 CPU`, `256 GB RAM`): `$1,000/month`

Live trading nodes:

- `L-MICRO` (`1 CPU`, `0.5 GB RAM`): `$24/month`
- `L1-1` (`1 CPU`, `1 GB RAM`): `$48/month`
- `L1-2` (`1 CPU`, `2 GB RAM`): `$78/month`
- `L2-4` (`2 CPU`, `4 GB RAM`): `$96/month`
- `L4-8` (`4 CPU`, `8 GB RAM`): `$200/month`
- `L8-16-GPU` (`8 CPU`, `16 GB RAM`): `$400/month`
- `L24-128-GPU` (`24 CPU`, `128 GB RAM`): `$1,000/month`

Assistant nodes:

- `A1-1` (`1 CPU`, `12 GB RAM`): `$24/month`
- `A4-12` (`4 CPU`, `12 GB RAM`): `$96/month`
- `A16-32` (`16 CPU`, `32 GB RAM`): `$384/month`

## Estimate for a Very Large Universe

QuantConnect documents that each security subscription uses about `5 MB` of RAM.

If you track all U.S. companies with market cap above `$5B`, a reasonable ballpark is about `1,000` symbols.

Baseline subscription memory:

```text
1,000 symbols x 5 MB = ~5,000 MB = ~5 GB RAM
```

Expected real usage for this strategy is higher because the algorithm also adds:

- minute subscriptions
- 2-minute and 5-minute consolidators
- EMA indicators
- rolling bar history
- framework state
- history request overhead

Practical estimate for this strategy:

- raw subscription estimate: `~5 GB RAM`
- realistic working estimate: `~6 GB to 8+ GB RAM`

## Practical Interpretation

For this strategy, a very broad `$5B+` universe would likely be:

- possible on `B2-8` (`8 GB`) only if the implementation stays lean
- safer on `B4-12` (`12 GB`)
- more likely to need `B8-16` if the universe grows further or the strategy adds more indicators, features, or logging

## Charles Schwab Trading Charges

Verified against Schwab's official pricing pages on `August 21, 2026`.

For the current strategy shape (`long-only` U.S. equities, no options logic, no intentional leverage), the relevant Schwab charges are:

- listed U.S. stocks and ETFs: `$0` online commission
- broker-assisted listed stocks and ETFs: `$0` commission plus `$25` service charge
- options: `$0` base commission plus `$0.65` per contract

Margin-specific costs only apply if the account actually carries a margin debit balance.

Current published Schwab margin rates:

- `$0` to `$24,999.99` debit: `11.825%` effective annual rate
- `$25,000` to `$49,999.99` debit: `11.325%`
- `$50,000` to `$99,999.99` debit: `10.375%`
- `$100,000` to `$249,999.99` debit: `10.325%`
- `$250,000` to `$499,999.99` debit: `10.075%`

Practical read for this strategy:

- if trades are fully funded with cash and no margin loan remains after settlement, expected Schwab trading cost is typically `$0` commission
- if the strategy carries a settled borrowed balance, Schwab margin interest applies and accrues daily using the published annual rate
- if the strategy ever shorts hard-to-borrow names, Schwab can also charge stock borrow fees that vary by symbol and day

Useful daily interest approximation for small debit balances:

```text
daily margin interest ~= borrowed amount x 11.825% / 360
```

Example:

```text
$1,000 borrowed ~= $0.33/day
```

Sources:

- [Schwab pricing](https://www.schwab.com/pricing)
- [Schwab margin rates and requirements](https://www.schwab.com/margin/margin-rates-and-requirements)
- [Schwab pricing guide for individual investors](https://disclosures.schwab.com/SchwabDashboard/61330/REG23060.pdf)

## Notes

- This is not per-backtest metered billing.
- Costs rise if you need stronger or additional nodes.
- “Infinite universe” is not realistic in practice; the useful interpretation is “very broad universe.”
