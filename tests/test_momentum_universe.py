"""
tests/test_momentum_universe.py

Covers momentum_universe.py's scoring function (compute_momentum_row) and
the end-to-end orchestration (select_momentum_universe) against a fake
broker, so nothing here depends on real Alpaca network calls.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import momentum_universe


def _make_bars(closes: list[float], volumes: list[float] | None = None, freq: str = "D") -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(closes), freq=freq)
    volumes = volumes or [2_000_000] * len(closes)
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes, "volume": volumes}, index=idx
    )


def test_empty_bars_returns_none():
    bars = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    assert momentum_universe.compute_momentum_row("X", bars, 20.0, 1.0, 1_000_000, 30, 15) is None


def test_insufficient_window_bars_returns_none():
    # Only 5 daily bars total, all inside the 30-day window -> below min_window_bars
    bars = _make_bars([10.0] * 5)
    assert momentum_universe.compute_momentum_row("X", bars, 20.0, 1.0, 1_000_000, 30, 15) is None


def test_price_above_max_returns_none():
    bars = _make_bars([50.0] * 40)
    assert momentum_universe.compute_momentum_row("X", bars, 20.0, 1.0, 1_000_000, 30, 15) is None


def test_price_below_min_returns_none():
    bars = _make_bars([0.50] * 40)
    assert momentum_universe.compute_momentum_row("X", bars, 20.0, 1.0, 1_000_000, 30, 15) is None


def test_low_volume_returns_none():
    bars = _make_bars([10.0] * 40, volumes=[10_000] * 40)
    assert momentum_universe.compute_momentum_row("X", bars, 20.0, 1.0, 1_000_000, 30, 15) is None


def test_momentum_calc_and_row_shape():
    closes = [10.0] * 40
    closes[-1] = 12.0  # last close 20% above the rest of the window
    bars = _make_bars(closes)
    row = momentum_universe.compute_momentum_row("SIGA", bars, 20.0, 1.0, 1_000_000, 30, 15)
    assert row is not None
    assert row["Symbol"] == "SIGA"
    assert row["Close"] == 12.0
    assert row["Momentum_Pct"] == pytest.approx(20.0, abs=0.5)


def test_negative_momentum_is_still_scored():
    closes = [10.0] * 40
    closes[-1] = 8.0  # down 20%
    bars = _make_bars(closes)
    row = momentum_universe.compute_momentum_row("DOWN", bars, 20.0, 1.0, 1_000_000, 30, 15)
    assert row is not None
    assert row["Momentum_Pct"] < 0


class _FakeBroker:
    """Stands in for AlpacaBroker so select_momentum_universe can be tested without network access."""

    def __init__(self, universe: list[str], bars_by_symbol: dict[str, pd.DataFrame]):
        self._universe = universe
        self._bars = bars_by_symbol

    def get_tradable_universe(self, exchanges, marginable_only):
        return self._universe

    def get_bars_batch(self, symbols, timeframe, start, end, batch_size=100):
        return self._bars


def _rising(start_close: float, pct_gain: float, n: int = 40) -> pd.DataFrame:
    closes = [start_close] * (n - 1) + [start_close * (1 + pct_gain)]
    return _make_bars(closes)


def test_select_momentum_universe_ranks_descending_and_dedups_top_n():
    bars_by_symbol = {
        "HIGH": _rising(10.0, 0.30),   # +30%
        "MID": _rising(10.0, 0.10),    # +10%
        "LOW": _rising(10.0, 0.01),    # +1%
        "TOOEXPENSIVE": _rising(50.0, 0.30),  # filtered by price band
    }
    broker = _FakeBroker(universe=list(bars_by_symbol), bars_by_symbol=bars_by_symbol)

    df = momentum_universe.select_momentum_universe(broker, top_n=2)

    assert list(df.columns) == momentum_universe.RESULT_COLUMNS
    assert list(df["Symbol"]) == ["HIGH", "MID"]


def test_select_momentum_universe_empty_universe_returns_empty_dataframe_with_columns():
    broker = _FakeBroker(universe=[], bars_by_symbol={})
    df = momentum_universe.select_momentum_universe(broker)
    assert df.empty
    assert list(df.columns) == momentum_universe.RESULT_COLUMNS


def test_select_momentum_universe_skips_missing_and_scoring_errors():
    bars_by_symbol = {
        "OK": _rising(10.0, 0.05),
        # "MISSING" is in the universe but absent from the batch result on purpose
    }
    broker = _FakeBroker(universe=["OK", "MISSING"], bars_by_symbol=bars_by_symbol)

    df = momentum_universe.select_momentum_universe(broker)

    assert len(df) == 1
    assert df.iloc[0]["Symbol"] == "OK"
