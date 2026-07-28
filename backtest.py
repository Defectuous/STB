"""
backtest.py

Walk-forward simulation of a strategy over historical bars stored in
storage/db.py (backfilling from Alpaca first if the local range is
incomplete). Feeds the strategy exactly the bar window the live engine
would see at each step, simulates fills at bar close, and applies the
stop-loss against each bar's intrabar low.

Usage:
    python backtest.py --symbol SPY --start 2022-01-01 --end 2026-07-01
    python backtest.py --config config.json   # backtests every configured symbol
"""

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from config_loader import load_config
from storage import db
from strategy.base import Action

# Load only this project's .env (see main.py for why this is pinned rather
# than left to dotenv's default upward directory search).
load_dotenv(Path(__file__).resolve().parent / ".env")

log = logging.getLogger(__name__)


@dataclass
class Trade:
    action: str
    date: str
    price: float
    qty: float
    reason: str


@dataclass
class BacktestResult:
    symbol: str
    trades: list[Trade] = field(default_factory=list)
    start_price: float = 0.0
    end_price: float = 0.0
    start_cash: float = 0.0
    end_equity: float = 0.0

    @property
    def strategy_return_pct(self) -> float:
        return (self.end_equity / self.start_cash - 1) * 100 if self.start_cash else 0.0

    @property
    def buy_and_hold_return_pct(self) -> float:
        return (self.end_price / self.start_price - 1) * 100 if self.start_price else 0.0

    @property
    def round_trips(self) -> list[tuple[Trade, Trade]]:
        pairs = []
        open_buy = None
        for t in self.trades:
            if t.action == "BUY":
                open_buy = t
            elif t.action in ("SELL", "STOP") and open_buy is not None:
                pairs.append((open_buy, t))
                open_buy = None
        return pairs

    @property
    def win_rate_pct(self) -> float:
        trips = self.round_trips
        if not trips:
            return 0.0
        wins = sum(1 for buy, sell in trips if sell.price > buy.price)
        return wins / len(trips) * 100


def ensure_data(broker, conn, symbol: str, timeframe_str: str, timeframe, start: datetime, end: datetime) -> None:
    """Backfill the local store with Alpaca history for [start, end] if a broker is available."""
    if broker is None:
        return
    try:
        bars = broker.get_historical_bars(symbol, timeframe, start, end)
        if not bars.empty:
            n = db.save_bars(conn, symbol, timeframe_str, bars)
            log.info("%s: backfilled %d bars from Alpaca", symbol, n)
    except Exception as exc:
        log.warning("%s: could not backfill from Alpaca (%s) - using local data only", symbol, exc)


def run_backtest(symbol: str, bars: pd.DataFrame, strategy, initial_cash: float = 10_000.0) -> BacktestResult:
    result = BacktestResult(symbol=symbol, start_cash=initial_cash)
    required = strategy.required_bars()

    if len(bars) <= required:
        raise ValueError(f"{symbol}: only {len(bars)} bars available, need > {required}")

    cash = initial_cash
    position: dict | None = None
    result.start_price = float(bars["close"].iloc[required])

    for i in range(required, len(bars)):
        window = bars.iloc[: i + 1]
        current = bars.iloc[i]
        date_str = str(bars.index[i])

        # Hard stop-loss check first - triggers on intrabar low, independent of the strategy signal.
        if position is not None and current["low"] <= position["stop_loss_price"]:
            exit_price = position["stop_loss_price"]
            cash += position["qty"] * exit_price
            result.trades.append(Trade("STOP", date_str, exit_price, position["qty"], "stop-loss hit"))
            position = None
            continue

        has_position = position is not None
        signal = strategy.generate_signal(symbol, window, has_position)

        if signal.action == Action.BUY and position is None:
            allocation = signal.allocation_pct if signal.allocation_pct is not None else 1.0
            qty = (cash * allocation) / signal.price
            cash -= qty * signal.price
            position = {"qty": qty, "stop_loss_price": signal.stop_loss_price}
            result.trades.append(Trade("BUY", date_str, signal.price, qty, signal.reason))

        elif signal.action == Action.SELL and position is not None:
            cash += position["qty"] * signal.price
            result.trades.append(Trade("SELL", date_str, signal.price, position["qty"], signal.reason))
            position = None

    result.end_price = float(bars["close"].iloc[-1])
    result.end_equity = cash + (position["qty"] * result.end_price if position else 0.0)
    return result


def print_report(result: BacktestResult) -> None:
    print(f"\n=== Backtest: {result.symbol} ===")
    print(f"Trades: {len(result.trades)}  |  Round trips: {len(result.round_trips)}  |  Win rate: {result.win_rate_pct:.1f}%")
    print(f"Strategy return: {result.strategy_return_pct:+.2f}%  (${result.start_cash:,.2f} -> ${result.end_equity:,.2f})")
    print(f"Buy & hold return: {result.buy_and_hold_return_pct:+.2f}%")
    for t in result.trades:
        print(f"  {t.date}  {t.action:5s}  {t.qty:>12.4f} @ ${t.price:>9.2f}   {t.reason}")


def run_aggregate_backtest(
    broker,
    symbols: list[str],
    strategy,
    start: datetime,
    end: datetime,
    initial_cash: float = 10_000.0,
    batch_size: int = 100,
    conn=None,
    timeframe_str: str | None = None,
) -> list[BacktestResult]:
    """
    Backtest `strategy` independently against each symbol in `symbols`,
    fetching bars via batched Alpaca requests (a handful of multi-symbol
    calls) rather than one request per symbol - the only practical way to
    backtest against a large dynamic universe like momentum_universe.py's
    top-N rather than a handful of hand-picked names.

    When conn/timeframe_str are given, every fetched symbol's bars are also
    written through to the local bar store (same table ensure_data uses),
    so a later --no-fetch run against the same symbols needs no network.
    """
    from alpaca.data.timeframe import TimeFrame

    bars_by_symbol = broker.get_bars_batch(symbols, TimeFrame.Day, start, end, batch_size=batch_size)

    if conn is not None and timeframe_str is not None:
        for symbol, bars in bars_by_symbol.items():
            n = db.save_bars(conn, symbol, timeframe_str, bars)
            log.info("%s: cached %d bars locally", symbol, n)

    results = []
    for symbol in symbols:
        bars = bars_by_symbol.get(symbol)
        if bars is None or bars.empty:
            continue
        try:
            results.append(run_backtest(symbol, bars, strategy, initial_cash=initial_cash))
        except ValueError as exc:
            log.warning(str(exc))
    return results


def load_cached_bars_batch(conn, symbols: list[str], timeframe_str: str, start: datetime, end: datetime) -> dict[str, pd.DataFrame]:
    """Local-only counterpart to run_aggregate_backtest's live fetch, for --no-fetch replays."""
    bars_by_symbol = {}
    for symbol in symbols:
        bars = db.load_bars(conn, symbol, timeframe_str, start=start.isoformat(), end=end.isoformat())
        if not bars.empty:
            bars_by_symbol[symbol] = bars
    return bars_by_symbol


def print_aggregate_report(label: str, results: list[BacktestResult]) -> None:
    print(f"\n=== Aggregate backtest: {label} ({len(results)} symbols with data) ===")
    if not results:
        print("No symbols had enough data to backtest.")
        return

    all_trips = [trip for r in results for trip in r.round_trips]
    wins = sum(1 for buy, sell in all_trips if sell.price > buy.price)
    returns = [r.strategy_return_pct for r in results]
    bh_returns = [r.buy_and_hold_return_pct for r in results]

    win_rate = (wins / len(all_trips) * 100) if all_trips else 0.0
    print(f"Round trips: {len(all_trips)}  |  Win rate: {win_rate:.1f}%")
    print(f"Strategy return  -  mean: {sum(returns) / len(returns):+.2f}%   median: {sorted(returns)[len(returns) // 2]:+.2f}%")
    print(f"Buy & hold return -  mean: {sum(bh_returns) / len(bh_returns):+.2f}%")
    print(f"Symbols net positive: {sum(1 for r in returns if r > 0)}/{len(returns)}")

    ranked = sorted(results, key=lambda r: r.strategy_return_pct, reverse=True)
    print("\nTop 5:")
    for r in ranked[:5]:
        print(f"  {r.symbol:6s}  {r.strategy_return_pct:+8.2f}%   ({len(r.round_trips)} round trips, {r.win_rate_pct:.0f}% win rate)")
    print("Bottom 5:")
    for r in ranked[-5:]:
        print(f"  {r.symbol:6s}  {r.strategy_return_pct:+8.2f}%   ({len(r.round_trips)} round trips, {r.win_rate_pct:.0f}% win rate)")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest a strategy against stored/historical bars")
    parser.add_argument("--config", default="config.json", help="Path to config JSON (default: config.json)")
    parser.add_argument("--symbol", action="append", help="Symbol to backtest (repeatable). Defaults to config's symbols.")
    parser.add_argument("--start", default="2020-01-01", help="Start date YYYY-MM-DD (default: 2020-01-01)")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--cash", type=float, default=10_000.0, help="Starting cash per symbol (default: 10000)")
    parser.add_argument("--no-fetch", action="store_true", help="Skip backfilling from Alpaca; use local DB data only")
    parser.add_argument(
        "--momentum-lookback-days", type=int, action="append",
        help="Instead of --symbol/config symbols, backtest against today's top-N momentum universe for this "
             "lookback window (repeatable, e.g. --momentum-lookback-days 30 --momentum-lookback-days 60)",
    )
    parser.add_argument("--momentum-top-n", type=int, default=100, help="Universe size per lookback window (default: 100)")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    args = _parse_args()
    config = load_config(args.config)
    symbols = args.symbol or config.symbols

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc) if args.end else datetime.now(timezone.utc)

    conn = db.get_connection(config.db_file)
    timeframe = config.strategy.timeframe()
    timeframe_str = str(timeframe)

    broker = None
    if not args.no_fetch:
        try:
            from broker.alpaca_broker import AlpacaBroker
            broker = AlpacaBroker(paper=config.paper_trading)
        except Exception as exc:
            log.warning("Alpaca credentials unavailable (%s) - backtesting on local DB data only", exc)

    if args.momentum_lookback_days:
        universe_cfg = config.universe

        for lookback in args.momentum_lookback_days:
            label = f"{lookback}-day momentum, top {args.momentum_top_n}"

            if args.no_fetch:
                snapshot_date = db.latest_momentum_snapshot_date(conn, lookback)
                if snapshot_date is None:
                    log.error(
                        "%d-day momentum: no cached snapshot in %s - run once without --no-fetch first",
                        lookback, config.db_file,
                    )
                    continue
                df = db.load_momentum_snapshot(conn, lookback)
                momentum_symbols = df["Symbol"].tolist()
                log.info("%d-day momentum universe: %d symbols (cached snapshot from %s)", lookback, len(momentum_symbols), snapshot_date)
                bars_by_symbol = load_cached_bars_batch(conn, momentum_symbols, timeframe_str, start, end)
                results = []
                for symbol in momentum_symbols:
                    bars = bars_by_symbol.get(symbol)
                    if bars is None or bars.empty:
                        continue
                    try:
                        results.append(run_backtest(symbol, bars, config.strategy, initial_cash=args.cash))
                    except ValueError as exc:
                        log.warning(str(exc))
                print_aggregate_report(f"{label} [cached snapshot {snapshot_date}]", results)
                continue

            if broker is None:
                log.error("%d-day momentum: Alpaca access unavailable and no cached snapshot requested (--no-fetch)", lookback)
                continue

            from momentum_universe import select_momentum_universe

            df = select_momentum_universe(
                broker,
                lookback_days=lookback,
                top_n=args.momentum_top_n,
                max_price=universe_cfg.max_price if universe_cfg else 20.00,
                min_price=universe_cfg.min_price if universe_cfg else 1.00,
                min_avg_volume=universe_cfg.min_avg_volume if universe_cfg else 1_000_000,
            )
            momentum_symbols = df["Symbol"].tolist()
            log.info("%d-day momentum universe: %d symbols", lookback, len(momentum_symbols))
            if not momentum_symbols:
                print(f"\n=== Aggregate backtest: {label} (0 symbols) ===")
                print("No symbols matched the momentum screen.")
                continue

            snapshot_date = datetime.now(timezone.utc).date().isoformat()
            n_saved = db.save_momentum_snapshot(conn, snapshot_date, lookback, df)
            log.info("%d-day momentum: cached %d-row snapshot as of %s", lookback, n_saved, snapshot_date)

            results = run_aggregate_backtest(
                broker, momentum_symbols, config.strategy, start, end,
                initial_cash=args.cash, conn=conn, timeframe_str=timeframe_str,
            )
            print_aggregate_report(label, results)
        return

    for symbol in symbols:
        ensure_data(broker, conn, symbol, timeframe_str, timeframe, start, end)
        bars = db.load_bars(conn, symbol, timeframe_str, start=start.isoformat(), end=end.isoformat())

        if bars.empty:
            log.error("%s: no bar data available (fetch failed and nothing stored locally)", symbol)
            continue

        try:
            result = run_backtest(symbol, bars, config.strategy, initial_cash=args.cash)
            print_report(result)
        except ValueError as exc:
            log.error(str(exc))


if __name__ == "__main__":
    main()
