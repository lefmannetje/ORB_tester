# NinjaTrader 8 ORB Strategy Tester (Import Guide)

## Files
- `ORBStrategyTester.cs`

## What it does
- Builds OR from `06:30:00` to `06:44:59`.
- Waits for 5m close breakout from `06:45:00` to `08:30:00`.
- Enters on next 1-minute bar.
- Stop = `OR opposite side ± StopBufferOr * ORRange`.
- Target = `Entry ± TargetOr * ORRange`.
- Max 2 trades/day.
- Optional: opposite-only second trade + 0.5 OR reset requirement.

## Install in NT8
1. Open **NinjaTrader 8**.
2. Go to **New > NinjaScript Editor**.
3. Right-click **Strategies** > **New Strategy**.
4. Replace generated code with contents of `ORBStrategyTester.cs`.
5. Press **Compile**.

## Backtest
1. Open **New > Strategy Analyzer**.
2. Select strategy: `ORBStrategyTester`.
3. Instrument: `MES`, `MNQ`, or `MGC`.
4. Data Series: 5 Minute (primary).
5. Set parameters (recommended baseline):
   - Threshold OR = `0.10`
   - Stop Buffer OR = `0.25`
   - Target OR = `0.75`
   - Use Fade Mode = `False`
   - Max Trades Per Day = `2`
   - Opposite Only Second Trade = `True`
   - Require 0.5 Reset For Second = `True`
6. Run, inspect Trades/Performance graphs.

## Note on your question
Yes: signal is confirmed on 5-minute close, then entry is executed on the next 1-minute bar.
