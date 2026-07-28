"""
tests/test_screener.py

Covers screener.py's scoring function (compute_screen_row) and the
end-to-end orchestration (run_screener) against a fake broker, so nothing
here depends on real Alpaca network calls.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import screener


def _make_bars(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    volumes = volumes or [2_000_000] * len(closes)
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes, "volume": volumes}, index=idx
    )


def test_insufficient_bars_returns_none():
    bars = _make_bars([10.0] * 50)
    assert screener.compute_screen_row("X", bars, 1.0, 20.0, 1_000_000, 20, 50, 200, 300.0) is None


def test_price_outside_band_returns_none():
    bars = _make_bars([50.0] * 206)
    assert screener.compute_screen_row("X", bars, 1.0, 20.0, 1_000_000, 20, 50, 200, 300.0) is None


def test_low_volume_returns_none():
    bars = _make_bars([10.0] * 206, volumes=[10_000] * 206)
    assert screener.compute_screen_row("X", bars, 1.0, 20.0, 1_000_000, 20, 50, 200, 300.0) is None


def test_golden_cross_flags_signal_triggered():
    flat = [10.0] * 205
    bars = _make_bars(flat + [10.15])  # single-bar pop right after the flat seed forces the cross on the last bar
    row = screener.compute_screen_row("SIGA", bars, 1.0, 20.0, 1_000_000, 20, 50, 200, 300.0)
    assert row is not None
    assert row["Status"] == "SIGNAL_TRIGGERED"
    assert row["Max_Whole_Shares"] == int(300.0 // row["Close"])


def test_watchlist_within_two_percent():
    idx = pd.date_range("2024-01-01", periods=201, freq="D")
    closes = [10.0] * 201
    bars = pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes, "volume": [2_000_000] * 201}, index=idx
    )

    def fake_sma(series, period):
        return pd.Series({20: 9.9, 50: 10.0, 200: 9.0}[period], index=range(len(series)))

    with patch("screener.sma", side_effect=fake_sma):
        row = screener.compute_screen_row("WATCH", bars, 1.0, 20.0, 1_000_000, 20, 50, 200, 300.0)
    assert row is not None
    assert row["Status"] == "WATCHLIST"


def test_watchlist_beyond_two_percent_is_discarded():
    idx = pd.date_range("2024-01-01", periods=201, freq="D")
    closes = [10.0] * 201
    bars = pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes, "volume": [2_000_000] * 201}, index=idx
    )

    def fake_sma(series, period):
        return pd.Series({20: 9.5, 50: 10.0, 200: 9.0}[period], index=range(len(series)))

    with patch("screener.sma", side_effect=fake_sma):
        row = screener.compute_screen_row("TOOFAR", bars, 1.0, 20.0, 1_000_000, 20, 50, 200, 300.0)
    assert row is None


class _FakeBroker:
    """Stands in for AlpacaBroker so run_screener can be tested without network access."""

    def __init__(self, universe: list[str], bars_by_symbol: dict[str, pd.DataFrame]):
        self._universe = universe
        self._bars = bars_by_symbol

    def get_tradable_universe(self, exchanges, marginable_only):
        return self._universe

    def get_bars_batch(self, symbols, timeframe, start, end, batch_size=100):
        return self._bars


def test_run_screener_end_to_end():
    flat = [10.0] * 205
    bars_by_symbol = {
        "SIGA": _make_bars(flat + [10.15]),   # crosses -> SIGNAL_TRIGGERED
        "FLATB": _make_bars(flat),             # never crosses -> discarded
        "CHEAP": _make_bars([0.50] * 206),     # below price band -> discarded
        # "MISSING" is in the universe but absent from the batch result on purpose
    }
    broker = _FakeBroker(universe=["SIGA", "FLATB", "CHEAP", "MISSING"], bars_by_symbol=bars_by_symbol)

    df = screener.run_screener(broker)

    assert list(df.columns) == screener.RESULT_COLUMNS
    assert len(df) == 1
    assert df.iloc[0]["Symbol"] == "SIGA"
    assert df.iloc[0]["Status"] == "SIGNAL_TRIGGERED"


def test_run_screener_empty_universe_returns_empty_dataframe_with_columns():
    broker = _FakeBroker(universe=[], bars_by_symbol={})
    df = screener.run_screener(broker)
    assert df.empty
    assert list(df.columns) == screener.RESULT_COLUMNS


def test_run_screener_sorts_signal_triggered_before_watchlist():
    # compute_screen_row is already verified independently above; here we only
    # need to check run_screener's sort order, so short-circuit the scoring
    # step with canned rows keyed by symbol.
    canned = {
        "WATCH_SYM": {"Symbol": "WATCH_SYM", "Status": "WATCHLIST", "Close": 10.0,
                      "SMA20": 9.9, "SMA50": 10.0, "SMA200": 9.0, "Avg_Volume_30D": 2_000_000, "Max_Whole_Shares": 30},
        "SIGNAL_SYM": {"Symbol": "SIGNAL_SYM", "Status": "SIGNAL_TRIGGERED", "Close": 10.15,
                       "SMA20": 10.01, "SMA50": 10.0, "SMA200": 9.0, "Avg_Volume_30D": 2_000_000, "Max_Whole_Shares": 29},
    }
    bars_by_symbol = {sym: _make_bars([10.0] * 206) for sym in canned}
    # Deliberately register WATCH_SYM before SIGNAL_SYM in the universe to prove
    # the output order comes from the Status sort, not universe/dict order.
    broker = _FakeBroker(universe=["WATCH_SYM", "SIGNAL_SYM"], bars_by_symbol=bars_by_symbol)

    with patch("screener.compute_screen_row", side_effect=lambda symbol, *a, **k: canned[symbol]):
        df = screener.run_screener(broker)

    assert list(df["Symbol"]) == ["SIGNAL_SYM", "WATCH_SYM"]
