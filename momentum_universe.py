"""
momentum_universe.py

Selects a dynamic trading universe: the top-N momentum names, under a price
cap, over a trailing lookback window - e.g. "top 100 momentum stocks under
$30 for the past 30 days."

Universe: active, tradable, marginable equities on NASDAQ/NYSE/AMEX (same
base universe as screener.py). For each symbol with enough history, computes
the trailing lookback-day return and a liquidity filter, keeps names under
the price cap, and returns the top-N ranked by return descending.

Usage:
    python momentum_universe.py
    python momentum_universe.py --limit 300     # quick pass over a subset, for testing
    python momentum_universe.py --top-n 50 --max-price 15
"""

import argparse
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from alpaca.data.timeframe import TimeFrame
from dotenv import load_dotenv

# Load only this project's .env (see main.py for why this is pinned rather
# than left to dotenv's default upward directory search).
load_dotenv(Path(__file__).resolve().parent / ".env")

log = logging.getLogger(__name__)

RESULT_COLUMNS = ["Symbol", "Momentum_Pct", "Close", "Avg_Volume", "Window_Bars"]


def compute_momentum_row(
    symbol: str,
    bars: pd.DataFrame,
    max_price: float,
    min_price: float,
    min_avg_volume: float,
    lookback_days: int,
    min_window_bars: int,
) -> dict | None:
    """
    Score a single symbol's bar history for momentum ranking.

    Returns a result row dict, or None if the symbol should be discarded
    (insufficient data in the lookback window, fails the price band, or
    fails the liquidity filter).
    """
    if bars.empty:
        return None

    end_ts = bars.index[-1]
    window_start = end_ts - timedelta(days=lookback_days)
    window = bars[bars.index > window_start]

    if len(window) < min_window_bars:
        return None

    price = float(window["close"].iloc[-1])
    if not (min_price <= price <= max_price):
        return None

    avg_volume = float(window["volume"].mean())
    if avg_volume <= min_avg_volume:
        return None

    start_price = float(window["close"].iloc[0])
    if start_price <= 0:
        return None

    momentum_pct = (price - start_price) / start_price * 100

    return {
        "Symbol": symbol,
        "Momentum_Pct": round(momentum_pct, 2),
        "Close": round(price, 2),
        "Avg_Volume": int(avg_volume),
        "Window_Bars": len(window),
    }


def select_momentum_universe(
    broker,
    exchanges: tuple[str, ...] = ("NASDAQ", "NYSE", "AMEX"),
    marginable_only: bool = True,
    lookback_days: int = 30,
    min_window_bars: int = 15,
    max_price: float = 30.00,
    min_price: float = 1.00,
    min_avg_volume: float = 1_000_000,
    top_n: int = 100,
    batch_size: int = 100,
    limit: int | None = None,
) -> pd.DataFrame:
    """
    Run the full momentum screen and return a DataFrame of the top_n symbols
    sorted by Momentum_Pct descending. Always returns a DataFrame with
    RESULT_COLUMNS, even when nothing matches.
    """
    symbols = broker.get_tradable_universe(exchanges=exchanges, marginable_only=marginable_only)
    if limit is not None:
        symbols = symbols[:limit]

    log.info("Ranking momentum for %d symbols across %s", len(symbols), ", ".join(exchanges))
    if not symbols:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    end = datetime.now(timezone.utc)
    # small buffer beyond the lookback window so a short window-start bar
    # (weekend/holiday gap) doesn't starve compute_momentum_row of bars
    start = end - timedelta(days=lookback_days + 10)
    bars_by_symbol = broker.get_bars_batch(symbols, TimeFrame.Day, start, end, batch_size=batch_size)

    rows = []
    for symbol in symbols:
        bars = bars_by_symbol.get(symbol)
        if bars is None or bars.empty:
            continue
        try:
            row = compute_momentum_row(
                symbol, bars, max_price, min_price, min_avg_volume, lookback_days, min_window_bars
            )
        except Exception:
            log.exception("Error scoring %s - skipping", symbol)
            continue
        if row is not None:
            rows.append(row)

    if not rows:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    df = pd.DataFrame(rows, columns=RESULT_COLUMNS)
    df = df.sort_values(by="Momentum_Pct", ascending=False).head(top_n).reset_index(drop=True)
    return df


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rank Alpaca's tradable universe by trailing momentum")
    parser.add_argument("--exchanges", nargs="+", default=["NASDAQ", "NYSE", "AMEX"], help="Exchanges to include")
    parser.add_argument("--no-marginable-filter", action="store_true", help="Include non-marginable assets")
    parser.add_argument("--lookback-days", type=int, default=30, help="Calendar days of trailing momentum window (default: 30)")
    parser.add_argument("--min-price", type=float, default=1.00, help="Minimum close price (default: 1.00)")
    parser.add_argument("--max-price", type=float, default=30.00, help="Maximum close price (default: 30.00)")
    parser.add_argument("--min-volume", type=float, default=1_000_000, help="Minimum average volume over the window (default: 1,000,000)")
    parser.add_argument("--top-n", type=int, default=100, help="Number of top-momentum symbols to keep (default: 100)")
    parser.add_argument("--batch-size", type=int, default=100, help="Symbols per historical-data request (default: 100)")
    parser.add_argument("--limit", type=int, default=None, help="Only rank the first N universe symbols (useful for a quick test run)")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    args = _parse_args()

    from broker.alpaca_broker import AlpacaBroker

    broker = AlpacaBroker(paper=True)
    df = select_momentum_universe(
        broker,
        exchanges=tuple(args.exchanges),
        marginable_only=not args.no_marginable_filter,
        lookback_days=args.lookback_days,
        min_price=args.min_price,
        max_price=args.max_price,
        min_avg_volume=args.min_volume,
        top_n=args.top_n,
        batch_size=args.batch_size,
        limit=args.limit,
    )

    if df.empty:
        print("No symbols matched the momentum screen.")
        return

    pd.set_option("display.width", 140)
    pd.set_option("display.max_rows", None)
    print(f"\n{len(df)} match(es):\n")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
