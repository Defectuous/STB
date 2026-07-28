"""
screener.py

Custom stock screener over Alpaca's tradable US-equity universe.

Universe: active, tradable, marginable equities on NASDAQ/NYSE/AMEX (OTC
excluded by construction). For each symbol with enough history, computes
20/50/200-period SMAs and flags:

  - SIGNAL_TRIGGERED: the 20 SMA crossed above the 50 SMA today.
  - WATCHLIST: the 20 SMA is below the 50 SMA but within 2% of crossing.

Everything else is discarded. Results are further constrained to a
$1-$20 price band, >1,000,000 30-day average volume, and price above the
200 SMA (macro uptrend filter), then reported with a Max_Whole_Shares
column sized off a fixed $300 capital base.

Usage:
    python screener.py
    python screener.py --limit 300          # quick pass over a subset, for testing
    python screener.py --min-price 2 --max-price 15 --min-volume 2000000
"""

import argparse
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from alpaca.data.timeframe import TimeFrame
from dotenv import load_dotenv

from strategy.indicators import crossed_above, pct_distance, sma

# Load only this project's .env (see main.py for why this is pinned rather
# than left to dotenv's default upward directory search).
load_dotenv(Path(__file__).resolve().parent / ".env")

log = logging.getLogger(__name__)

RESULT_COLUMNS = ["Symbol", "Status", "Close", "SMA20", "SMA50", "SMA200", "Avg_Volume_30D", "Max_Whole_Shares"]


def compute_screen_row(
    symbol: str,
    bars: pd.DataFrame,
    min_price: float,
    max_price: float,
    min_avg_volume: float,
    fast_period: int,
    slow_period: int,
    long_period: int,
    capital: float,
) -> dict | None:
    """
    Score a single symbol's bar history against the screener criteria.

    Returns a result row dict for SIGNAL_TRIGGERED/WATCHLIST symbols that
    also pass the price/volume/trend filters, or None if the symbol should
    be discarded (insufficient data or fails any filter).
    """
    required = long_period + 1  # long_period bars to seed the SMA, +1 to compare t vs t-1
    if len(bars) < required:
        return None

    closes = bars["close"]
    sma_fast = sma(closes, fast_period)
    sma_slow = sma(closes, slow_period)
    sma_long = sma(closes, long_period)

    fast_now, fast_prev = sma_fast.iloc[-1], sma_fast.iloc[-2]
    slow_now, slow_prev = sma_slow.iloc[-1], sma_slow.iloc[-2]
    long_now = sma_long.iloc[-1]

    if pd.isna(fast_now) or pd.isna(fast_prev) or pd.isna(slow_now) or pd.isna(slow_prev) or pd.isna(long_now):
        return None

    price = float(closes.iloc[-1])
    if not (min_price <= price <= max_price):
        return None

    avg_volume_30d = float(bars["volume"].tail(30).mean())
    if avg_volume_30d <= min_avg_volume:
        return None

    if not (price > long_now):
        return None

    if crossed_above(fast_prev, slow_prev, fast_now, slow_now):
        status = "SIGNAL_TRIGGERED"
    elif fast_now < slow_now and pct_distance(fast_now, slow_now) <= 0.02:
        status = "WATCHLIST"
    else:
        return None

    return {
        "Symbol": symbol,
        "Status": status,
        "Close": round(price, 2),
        "SMA20": round(fast_now, 2),
        "SMA50": round(slow_now, 2),
        "SMA200": round(long_now, 2),
        "Avg_Volume_30D": int(avg_volume_30d),
        "Max_Whole_Shares": int(capital // price),
    }


def run_screener(
    broker,
    exchanges: tuple[str, ...] = ("NASDAQ", "NYSE", "AMEX"),
    marginable_only: bool = True,
    lookback_days: int = 280,
    min_price: float = 1.00,
    max_price: float = 20.00,
    min_avg_volume: float = 1_000_000,
    fast_period: int = 20,
    slow_period: int = 50,
    long_period: int = 200,
    capital: float = 300.0,
    batch_size: int = 100,
    limit: int | None = None,
) -> pd.DataFrame:
    """
    Run the full screen and return a DataFrame sorted with SIGNAL_TRIGGERED
    rows first, then WATCHLIST. Always returns a DataFrame with
    RESULT_COLUMNS, even when nothing matches (empty universe, no fetchable
    data, or every symbol filtered out).
    """
    symbols = broker.get_tradable_universe(exchanges=exchanges, marginable_only=marginable_only)
    if limit is not None:
        symbols = symbols[:limit]

    log.info("Screening %d symbols across %s", len(symbols), ", ".join(exchanges))
    if not symbols:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    bars_by_symbol = broker.get_bars_batch(symbols, TimeFrame.Day, start, end, batch_size=batch_size)

    rows = []
    for symbol in symbols:
        bars = bars_by_symbol.get(symbol)
        if bars is None or bars.empty:
            continue
        try:
            row = compute_screen_row(
                symbol, bars, min_price, max_price, min_avg_volume, fast_period, slow_period, long_period, capital
            )
        except Exception:
            log.exception("Error scoring %s - skipping", symbol)
            continue
        if row is not None:
            rows.append(row)

    if not rows:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    df = pd.DataFrame(rows, columns=RESULT_COLUMNS)
    status_rank = {"SIGNAL_TRIGGERED": 0, "WATCHLIST": 1}
    df = df.sort_values(by="Status", key=lambda col: col.map(status_rank)).reset_index(drop=True)
    return df


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Screen Alpaca's tradable universe for SMA crossover setups")
    parser.add_argument("--exchanges", nargs="+", default=["NASDAQ", "NYSE", "AMEX"], help="Exchanges to include")
    parser.add_argument("--no-marginable-filter", action="store_true", help="Include non-marginable assets")
    parser.add_argument("--lookback-days", type=int, default=280, help="Calendar days of daily bars to fetch (default: 280)")
    parser.add_argument("--min-price", type=float, default=1.00, help="Minimum close price (default: 1.00)")
    parser.add_argument("--max-price", type=float, default=20.00, help="Maximum close price (default: 20.00)")
    parser.add_argument("--min-volume", type=float, default=1_000_000, help="Minimum 30-day average volume (default: 1,000,000)")
    parser.add_argument("--fast-period", type=int, default=20, help="Fast SMA period (default: 20)")
    parser.add_argument("--slow-period", type=int, default=50, help="Slow SMA period (default: 50)")
    parser.add_argument("--long-period", type=int, default=200, help="Macro-trend SMA period (default: 200)")
    parser.add_argument("--capital", type=float, default=300.0, help="Fixed capital base for Max_Whole_Shares (default: 300)")
    parser.add_argument("--batch-size", type=int, default=100, help="Symbols per historical-data request (default: 100)")
    parser.add_argument("--limit", type=int, default=None, help="Only screen the first N universe symbols (useful for a quick test run)")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    args = _parse_args()

    from broker.alpaca_broker import AlpacaBroker

    broker = AlpacaBroker(paper=True)
    df = run_screener(
        broker,
        exchanges=tuple(args.exchanges),
        marginable_only=not args.no_marginable_filter,
        lookback_days=args.lookback_days,
        min_price=args.min_price,
        max_price=args.max_price,
        min_avg_volume=args.min_volume,
        fast_period=args.fast_period,
        slow_period=args.slow_period,
        long_period=args.long_period,
        capital=args.capital,
        batch_size=args.batch_size,
        limit=args.limit,
    )

    if df.empty:
        print("No symbols matched the screener criteria.")
        return

    pd.set_option("display.width", 140)
    pd.set_option("display.max_rows", None)
    print(f"\n{len(df)} match(es):\n")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
