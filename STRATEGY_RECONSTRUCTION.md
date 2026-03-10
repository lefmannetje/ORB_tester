# ORB strategy + 50k account growth simulation

This version focuses on a practical question: **can a 50k Tradeify-style account pass and grow** with this ORB strategy?

## Strategy setup used
- OR window: `06:30-06:44` PST
- Signal window: `06:45-08:30` PST
- Signal: 5m close outside OR by threshold
- Entry: next 1m open
- Stop: OR opposite side ± `0.25 * OR range`
- Max 2 trades/day, opposite-only second trade
- 0.5 OR reset required for second trade

## Two tested parameter sets

### Set A (stronger growth)
- `threshold=0.10`
- `stop_buffer=0.25`
- `target=0.75`

### Set B (more selective)
- `threshold=0.20`
- `stop_buffer=0.25`
- `target=0.75`

## 50k eval assumptions in simulator
- Start balance: `50,000`
- Profit target: `3,000`
- Max drawdown: `2,000`
- Risk per trade: `1%` (configurable)

## Best practical implementation guidance (from current data)

### Preferred TP/SL model
- **TP = 0.75 * OR range**
- **SL = 0.25 * OR extension beyond opposite OR edge**
- Use Set A first (`threshold=0.10`) for higher growth in this dataset.

### Ticker choices for real-life implementation
- **Use first:** `MES`, `MNQ`, `MGC` (best pass + growth profile)
- **Use cautiously / avoid for strict eval:** `MET`, `NQ`, `QQQ`

### Contract sizing (50k, 1% risk budget baseline)
Use the printed sizing guide in CLI output. A practical starter plan from observed runs:
- `MES`: start 1 micro contract, scale toward 2 only after cushion builds
- `MNQ`: start 1 micro contract only when stop distance allows it
- `MGC`: 1 contract only when risk distance is tight
- `NQ`: generally too large for strict 1% risk on 50k with this setup
- `QQQ`: shares are scalable but account still showed later drawdown breach in tests

## Commands

### Per-ticker 50k simulation (shows pass/fail + sizing)
```bash
python main.py --data all --threshold 0.1 --stop-buffer 0.25 --target 0.75 --mode breakout --second-policy opposite_only --trade-end 08:30 --simulate-eval --eval-risk-pct 0.01 --eval-profit-target 3000 --eval-max-drawdown 2000
```

### Combined one-account simulation + equity graph
```bash
python main.py --data all --threshold 0.1 --stop-buffer 0.25 --target 0.75 --mode breakout --second-policy opposite_only --trade-end 08:30 --simulate-eval --eval-risk-pct 0.01 --eval-profit-target 3000 --eval-max-drawdown 2000 --eval-combine-tickers --plot-equity artifacts/equity_all_50k.png
```

### Optional per-ticker optimization pass (coarse search)
```bash
python main.py --data all --optimize-50k --eval-profit-target 3000 --eval-max-drawdown 2000
```
