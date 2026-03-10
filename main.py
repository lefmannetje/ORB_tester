from __future__ import annotations

import argparse
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Iterable

import pandas as pd


@dataclass(frozen=True)
class Params:
    threshold_or: float = 0.20
    stop_buffer_or: float = 0.25
    target_or: float = 1.0
    mode: str = "breakout"  # breakout | fade
    second_trade_policy: str = "opposite_only"  # opposite_only | same_or_opposite
    require_half_reset_for_second: bool = True
    trade_end_time: str = "08:30"  # PST, aligns with "no trades after 11:30 EST"


@dataclass
class Trade:
    ticker: str
    day: pd.Timestamp
    side: int
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry: float
    exit: float
    stop: float
    target: float
    or_high: float
    or_low: float
    or_range: float
    result_r: float
    reason: str


def load_1m(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path).copy()
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_convert("America/Los_Angeles")
    return df.sort_values("date").reset_index(drop=True)


def to_5m(df_1m: pd.DataFrame) -> pd.DataFrame:
    return (
        df_1m.set_index("date")
        .resample("5min", label="right", closed="right")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )


def session_days(df: pd.DataFrame) -> Iterable[pd.Timestamp]:
    yield from df["date"].dt.floor("D").drop_duplicates()


def signal_from_close(close: float, or_high: float, or_low: float, or_range: float, p: Params) -> int:
    up_break = close >= or_high + p.threshold_or * or_range
    dn_break = close <= or_low - p.threshold_or * or_range
    if up_break:
        return 1 if p.mode == "breakout" else -1
    if dn_break:
        return -1 if p.mode == "breakout" else 1
    return 0


def simulate_day(df_1m_day: pd.DataFrame, df_5m_day: pd.DataFrame, p: Params, ticker: str) -> list[Trade]:
    trades: list[Trade] = []

    # Chat-derived windows (PST)
    or_window = df_1m_day[
        (df_1m_day["date"].dt.time >= pd.Timestamp("06:30").time())
        & (df_1m_day["date"].dt.time <= pd.Timestamp("06:44").time())
    ]
    if or_window.empty:
        return trades

    or_high = float(or_window["high"].max())
    or_low = float(or_window["low"].min())
    or_range = or_high - or_low
    if or_range <= 0:
        return trades

    trade_window_5m = df_5m_day[
        (df_5m_day["date"].dt.time >= pd.Timestamp("06:45").time())
        & (df_5m_day["date"].dt.time <= pd.Timestamp(p.trade_end_time).time())
    ]
    if trade_window_5m.empty:
        return trades

    reset_level = or_low + 0.5 * or_range

    for _, bar in trade_window_5m.iterrows():
        if len(trades) >= 2:
            break

        raw_signal = signal_from_close(float(bar["close"]), or_high, or_low, or_range, p)
        if raw_signal == 0:
            continue

        # 2-trade daily policy from chat notes
        if len(trades) == 1:
            if trades[0].result_r > 0:
                break
            if p.second_trade_policy == "opposite_only" and raw_signal == trades[0].side:
                continue
            if p.require_half_reset_for_second:
                after_first = df_1m_day[(df_1m_day["date"] > trades[0].exit_time) & (df_1m_day["date"] <= bar["date"]) ]
                reset_seen = (not after_first.empty) and bool(((after_first["low"] <= reset_level) & (after_first["high"] >= reset_level)).any())
                if not reset_seen:
                    continue

        entry_time = bar["date"] + pd.Timedelta(minutes=1)
        post = df_1m_day[df_1m_day["date"] >= entry_time]
        if post.empty:
            continue

        side = raw_signal
        entry = float(post.iloc[0]["open"])

        if side == 1:
            stop = or_low - p.stop_buffer_or * or_range
            target = entry + p.target_or * or_range
        else:
            stop = or_high + p.stop_buffer_or * or_range
            target = entry - p.target_or * or_range

        if (side == 1 and stop >= entry) or (side == -1 and stop <= entry):
            continue

        risk = abs(entry - stop)
        if risk == 0:
            continue

        session_end = pd.Timestamp(entry_time.date()).tz_localize(entry_time.tzinfo) + pd.Timedelta(hours=13)
        walk = post[post["date"] <= session_end]
        if walk.empty:
            continue

        exit_price = float(walk.iloc[-1]["close"])
        exit_time = walk.iloc[-1]["date"]
        reason = "session_close"

        for _, m in walk.iterrows():
            lo, hi, t = float(m["low"]), float(m["high"]), m["date"]
            if side == 1:
                if lo <= stop:
                    exit_price, exit_time, reason = stop, t, "stop"
                    break
                if hi >= target:
                    exit_price, exit_time, reason = target, t, "target"
                    break
            else:
                if hi >= stop:
                    exit_price, exit_time, reason = stop, t, "stop"
                    break
                if lo <= target:
                    exit_price, exit_time, reason = target, t, "target"
                    break

        result_r = ((exit_price - entry) / risk) * side
        trades.append(
            Trade(
                ticker=ticker,
                day=df_1m_day.iloc[0]["date"].floor("D"),
                side=side,
                entry_time=entry_time,
                exit_time=exit_time,
                entry=entry,
                exit=exit_price,
                stop=stop,
                target=target,
                or_high=or_high,
                or_low=or_low,
                or_range=or_range,
                result_r=float(result_r),
                reason=reason,
            )
        )

    return trades


def backtest_one(path: str, params: Params) -> pd.DataFrame:
    ticker = Path(path).stem.replace("-1m-clean", "")
    df_1m = load_1m(path)
    df_5m = to_5m(df_1m)

    all_trades: list[Trade] = []
    for day in session_days(df_1m):
        d1 = df_1m[df_1m["date"].dt.floor("D") == day]
        d5 = df_5m[df_5m["date"].dt.floor("D") == day]
        all_trades.extend(simulate_day(d1, d5, params, ticker))

    return pd.DataFrame([t.__dict__ for t in all_trades])


def summarize(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"trades": 0, "win_rate": 0.0, "avg_r": 0.0, "sum_r": 0.0}
    return {
        "trades": int(len(trades)),
        "win_rate": float((trades["result_r"] > 0).mean()),
        "avg_r": float(trades["result_r"].mean()),
        "sum_r": float(trades["result_r"].sum()),
    }


def split_train_test(df: pd.DataFrame, split: float = 0.7) -> tuple[pd.DataFrame, pd.DataFrame]:
    if df.empty:
        return df.copy(), df.copy()
    days = sorted(df["day"].drop_duplicates())
    cut = max(1, int(len(days) * split))
    train_days = set(days[:cut])
    train = df[df["day"].isin(train_days)].copy()
    test = df[~df["day"].isin(train_days)].copy()
    return train, test


def score_robust(train: pd.DataFrame, test: pd.DataFrame) -> float:
    if train.empty or test.empty:
        return -1e9
    # prefer high out-of-sample R, then stability
    return float(test["result_r"].sum() + 0.25 * train["result_r"].sum() + 5 * ((test["result_r"] > 0).mean()))


def discover_best(paths: list[str]) -> pd.DataFrame:
    rows: list[dict] = []
    grid = product(
        [0.1, 0.2, 0.25],
        [0.15, 0.25],
        [0.75, 1.0],
        ["breakout"],
        ["opposite_only"],
    )

    for th, sl, tp, mode, second_policy in grid:
        p = Params(threshold_or=th, stop_buffer_or=sl, target_or=tp, mode=mode, second_trade_policy=second_policy, require_half_reset_for_second=True, trade_end_time="08:30")

        combined_train = []
        combined_test = []
        ticker_pass = 0
        for path in paths:
            tr = backtest_one(path, p)
            train, test = split_train_test(tr)
            combined_train.append(train)
            combined_test.append(test)
            if not test.empty and test["result_r"].sum() > 0:
                ticker_pass += 1

        train_all = pd.concat(combined_train, ignore_index=True) if combined_train else pd.DataFrame()
        test_all = pd.concat(combined_test, ignore_index=True) if combined_test else pd.DataFrame()
        score = score_robust(train_all, test_all)
        row = {
            "threshold": th,
            "sl_buffer": sl,
            "tp": tp,
            "mode": mode,
            "second_policy": second_policy,
            "tickers_profitable_oos": ticker_pass,
            "score": score,
            "train_sum_r": float(train_all["result_r"].sum()) if not train_all.empty else 0.0,
            "test_sum_r": float(test_all["result_r"].sum()) if not test_all.empty else 0.0,
            "test_win_rate": float((test_all["result_r"] > 0).mean()) if not test_all.empty else 0.0,
            "test_trades": int(len(test_all)),
        }
        rows.append(row)

    return pd.DataFrame(rows).sort_values(["score", "tickers_profitable_oos", "test_sum_r"], ascending=False)


def parse_data_paths(data_arg: str) -> list[str]:
    if data_arg == "all":
        return sorted(str(p) for p in Path("data").glob("*-1m-clean.parquet"))
    return [x.strip() for x in data_arg.split(",") if x.strip()]


def run_with_params(paths: list[str], p: Params) -> tuple[pd.DataFrame, pd.DataFrame]:
    summaries = []
    all_trades = []
    for path in paths:
        t = backtest_one(path, p)
        sm = summarize(t)
        ticker = Path(path).stem.replace("-1m-clean", "")
        summaries.append({"ticker": ticker, **sm})
        all_trades.append(t)
    return pd.DataFrame(summaries), pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()


@dataclass(frozen=True)
class EvalConfig:
    starting_balance: float = 50_000.0
    profit_target: float = 3_000.0
    max_drawdown: float = 2_000.0
    risk_per_trade_pct: float = 0.01


TICK_VALUE = {
    "MNQ": 2.0,
    "NQ": 20.0,
    "MES": 5.0,
    "MET": 5.0,
    "MGC": 10.0,
    "QQQ": 1.0,
}


def contracts_for_trade(trade_row: pd.Series, balance: float, cfg: EvalConfig) -> int:
    ticker = str(trade_row["ticker"])
    tick_value = TICK_VALUE.get(ticker, 1.0)
    risk_points = abs(float(trade_row["entry"]) - float(trade_row["stop"]))
    if risk_points <= 0:
        return 0
    risk_per_contract = risk_points * tick_value
    risk_budget = max(balance * cfg.risk_per_trade_pct, 0.0)
    return int(risk_budget // risk_per_contract)


def simulate_eval(trades: pd.DataFrame, cfg: EvalConfig, combine_tickers: bool = False) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()

    working = trades.copy().sort_values(["entry_time", "ticker"]).reset_index(drop=True)
    if combine_tickers:
        working["sim_ticker"] = "ALL"
    else:
        working["sim_ticker"] = working["ticker"]

    rows: list[dict] = []
    for sim_ticker, g in working.groupby("sim_ticker", sort=True):
        bal = cfg.starting_balance
        high_water = bal
        passed = False
        failed = False
        pass_time = None
        fail_time = None
        max_dd_seen = 0.0

        trades_taken = 0
        contracts_sum = 0
        max_contracts = 0

        for _, tr in g.iterrows():
            contracts = contracts_for_trade(tr, bal, cfg)
            if contracts <= 0:
                continue
            trades_taken += 1
            contracts_sum += contracts
            max_contracts = max(max_contracts, contracts)

            points = (float(tr["exit"]) - float(tr["entry"])) * int(tr["side"])
            pnl = points * TICK_VALUE.get(str(tr["ticker"]), 1.0) * contracts
            bal += pnl
            high_water = max(high_water, bal)
            drawdown = high_water - bal
            max_dd_seen = max(max_dd_seen, drawdown)

            if not passed and bal - cfg.starting_balance >= cfg.profit_target:
                passed = True
                pass_time = tr["exit_time"]
            if drawdown > cfg.max_drawdown:
                failed = True
                fail_time = tr["exit_time"]
                break

        rows.append({
            "ticker": sim_ticker,
            "start_balance": cfg.starting_balance,
            "end_balance": round(bal, 2),
            "net_pnl": round(bal - cfg.starting_balance, 2),
            "passed_eval": passed,
            "failed_drawdown": failed,
            "pass_time": pass_time,
            "fail_time": fail_time,
            "max_balance": round(high_water, 2),
            "max_drawdown_seen": round(max_dd_seen, 2),
            "risk_pct": cfg.risk_per_trade_pct,
            "trades_executed": trades_taken,
            "avg_contracts": round((contracts_sum / trades_taken), 2) if trades_taken else 0.0,
            "max_contracts": max_contracts,
        })

    return pd.DataFrame(rows)


def recommend_contracts(trades: pd.DataFrame, cfg: EvalConfig) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows = []
    for ticker, g in trades.groupby("ticker"):
        tv = TICK_VALUE.get(str(ticker), 1.0)
        risk_points = (g["entry"] - g["stop"]).abs()
        med_risk_points = float(risk_points.median())
        p75_risk_points = float(risk_points.quantile(0.75))
        med_risk_dollars = med_risk_points * tv
        p75_risk_dollars = p75_risk_points * tv
        risk_budget = cfg.starting_balance * cfg.risk_per_trade_pct
        contracts_med = int(risk_budget // med_risk_dollars) if med_risk_dollars > 0 else 0
        contracts_p75 = int(risk_budget // p75_risk_dollars) if p75_risk_dollars > 0 else 0
        rows.append({
            "ticker": ticker,
            "risk_budget_usd": round(risk_budget, 2),
            "median_risk_per_contract_usd": round(med_risk_dollars, 2),
            "p75_risk_per_contract_usd": round(p75_risk_dollars, 2),
            "contracts_median_risk": contracts_med,
            "contracts_p75_risk_safer": contracts_p75,
        })
    return pd.DataFrame(rows).sort_values("ticker")


def equity_curve_from_eval(trades: pd.DataFrame, cfg: EvalConfig, ticker_mode: str = "ALL") -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["time", "balance"])
    working = trades.copy().sort_values(["entry_time", "ticker"]).reset_index(drop=True)
    if ticker_mode != "ALL":
        working = working[working["ticker"] == ticker_mode].copy()
    if working.empty:
        return pd.DataFrame(columns=["time", "balance"])

    bal = cfg.starting_balance
    rows = [{"time": working.iloc[0]["entry_time"], "balance": bal}]
    for _, tr in working.iterrows():
        contracts = contracts_for_trade(tr, bal, cfg)
        if contracts <= 0:
            continue
        points = (float(tr["exit"]) - float(tr["entry"])) * int(tr["side"])
        pnl = points * TICK_VALUE.get(str(tr["ticker"]), 1.0) * contracts
        bal += pnl
        rows.append({"time": tr["exit_time"], "balance": bal})
    return pd.DataFrame(rows)


def optimize_for_50k_eval(paths: list[str], profit_target: float, drawdown: float) -> pd.DataFrame:
    grid = list(product(
        [0.1, 0.2],
        [0.25],
        [0.75],
        [0.005, 0.01],
        ["opposite_only"],
    ))
    rows = []
    for path in paths:
        ticker = Path(path).stem.replace("-1m-clean", "")
        best = None
        for th, sl, tp, risk_pct, second in grid:
            p = Params(
                threshold_or=th,
                stop_buffer_or=sl,
                target_or=tp,
                mode="breakout",
                second_trade_policy=second,
                require_half_reset_for_second=True,
                trade_end_time="08:30",
            )
            tr = backtest_one(path, p)
            cfg = EvalConfig(starting_balance=50_000.0, profit_target=profit_target, max_drawdown=drawdown, risk_per_trade_pct=risk_pct)
            ev = simulate_eval(tr, cfg, combine_tickers=False)
            if ev.empty:
                continue
            rec = ev.iloc[0].to_dict()
            score = (1 if rec["passed_eval"] else 0) * 1_000_000 + rec["net_pnl"] - (2000 if rec["failed_drawdown"] else 0)
            cand = {
                "ticker": ticker,
                "threshold": th,
                "stop_buffer": sl,
                "target": tp,
                "risk_pct": risk_pct,
                **rec,
                "score": score,
            }
            if best is None or cand["score"] > best["score"]:
                best = cand
        if best is not None:
            rows.append(best)
    return pd.DataFrame(rows).sort_values("score", ascending=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="ORB reverse-engineering and robust multi-ticker backtester")
    parser.add_argument("--data", default="all", help="'all' or comma-separated parquet file paths")
    parser.add_argument("--threshold", type=float, default=0.2)
    parser.add_argument("--stop-buffer", type=float, default=0.25)
    parser.add_argument("--target", type=float, default=1.0)
    parser.add_argument("--mode", choices=["breakout", "fade"], default="breakout")
    parser.add_argument("--second-policy", choices=["opposite_only", "same_or_opposite"], default="opposite_only")
    parser.add_argument("--trade-end", default="08:30", help="PST cutoff for new entries, e.g. 08:30")
    parser.add_argument("--no-half-reset", action="store_true", help="disable 0.5 OR reset requirement for 2nd trade")
    parser.add_argument("--discover", action="store_true", help="run robust parameter discovery")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--save-trades", default="")
    parser.add_argument("--simulate-eval", action="store_true", help="simulate Tradeify-like 50k evaluation")
    parser.add_argument("--eval-risk-pct", type=float, default=0.01, help="risk per trade fraction of balance")
    parser.add_argument("--eval-profit-target", type=float, default=3000.0)
    parser.add_argument("--eval-max-drawdown", type=float, default=2000.0)
    parser.add_argument("--eval-combine-tickers", action="store_true", help="simulate one account trading all tickers")
    parser.add_argument("--optimize-50k", action="store_true", help="find best per-ticker settings for 50k eval")
    parser.add_argument("--plot-equity", default="", help="png path for equity curve plot")
    args = parser.parse_args()

    paths = parse_data_paths(args.data)
    if not paths:
        raise ValueError("No parquet files found/selected.")

    if args.discover:
        ranked = discover_best(paths)
        print(ranked.head(args.top).to_string(index=False))
        return

    if args.optimize_50k:
        opt = optimize_for_50k_eval(paths, args.eval_profit_target, args.eval_max_drawdown)
        if opt.empty:
            print("No optimization results.")
            return
        show_cols = ["ticker","threshold","stop_buffer","target","risk_pct","passed_eval","failed_drawdown","net_pnl","end_balance","max_drawdown_seen"]
        print(opt[show_cols].to_string(index=False))
        return

    p = Params(
        threshold_or=args.threshold,
        stop_buffer_or=args.stop_buffer,
        target_or=args.target,
        mode=args.mode,
        second_trade_policy=args.second_policy,
        require_half_reset_for_second=not args.no_half_reset,
        trade_end_time=args.trade_end,
    )
    summary, trades = run_with_params(paths, p)
    print(summary.to_string(index=False))
    if not trades.empty:
        print("\nCOMBINED:", summarize(trades))
    if args.simulate_eval:
        cfg = EvalConfig(
            starting_balance=50_000.0,
            profit_target=args.eval_profit_target,
            max_drawdown=args.eval_max_drawdown,
            risk_per_trade_pct=args.eval_risk_pct,
        )
        eval_result = simulate_eval(trades, cfg, combine_tickers=args.eval_combine_tickers)
        if not eval_result.empty:
            print("\nEVAL SIMULATION:")
            print(eval_result.to_string(index=False))

        rec = recommend_contracts(trades, cfg)
        if not rec.empty:
            print("\nCONTRACT SIZING GUIDE (50k account):")
            print(rec.to_string(index=False))

        if args.plot_equity:
            import matplotlib.pyplot as plt
            mode = "ALL" if args.eval_combine_tickers else str(summary.iloc[0]["ticker"]) if len(summary)==1 else "ALL"
            eq = equity_curve_from_eval(trades, cfg, ticker_mode=mode)
            if not eq.empty:
                plt.figure(figsize=(10,4))
                plt.plot(eq["time"], eq["balance"])
                plt.title(f"Equity Curve ({mode})")
                plt.xlabel("Time")
                plt.ylabel("Balance")
                plt.tight_layout()
                Path(args.plot_equity).parent.mkdir(parents=True, exist_ok=True)
                plt.savefig(args.plot_equity)
                print(f"Saved equity curve to {args.plot_equity}")

    if args.save_trades:
        trades.to_csv(args.save_trades, index=False)
        print(f"Saved trades to {args.save_trades}")


if __name__ == "__main__":
    main()
