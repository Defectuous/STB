"""
tests/test_mac_strategy.py

Walks a synthetic price series (flat -> sharp uptrend -> sharp downtrend)
through MovingAverageCrossoverStrategy bar-by-bar, exactly as the live
engine and backtest.py would, and asserts a single golden-cross BUY fires
during the uptrend followed by a single death-cross SELL during the
downtrend.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy.base import Action
from strategy.mac_strategy import MovingAverageCrossoverStrategy


def _make_bars(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.001 for c in closes],
            "low": [c * 0.999 for c in closes],
            "close": closes,
            "volume": [1_000] * len(closes),
        },
        index=idx,
    )


def _walk(strategy: MovingAverageCrossoverStrategy, bars: pd.DataFrame) -> list[Action]:
    actions = []
    has_position = False
    for i in range(strategy.required_bars(), len(bars)):
        window = bars.iloc[: i + 1]
        signal = strategy.generate_signal("TEST", window, has_position)
        actions.append(signal.action)
        if signal.action == Action.BUY:
            has_position = True
        elif signal.action == Action.SELL:
            has_position = False
    return actions


def test_golden_cross_then_death_cross():
    flat = [100.0] * 6
    uptrend = [100.0 + i * 3 for i in range(1, 16)]     # 103 -> 145
    downtrend = [145.0 - i * 3 for i in range(1, 16)]   # 142 -> 100
    bars = _make_bars(flat + uptrend + downtrend)

    strategy = MovingAverageCrossoverStrategy(fast_period=3, slow_period=5, max_loss_pct=0.05)
    actions = _walk(strategy, bars)

    assert actions.count(Action.BUY) == 1, f"expected exactly one BUY, got {actions}"
    assert actions.count(Action.SELL) == 1, f"expected exactly one SELL, got {actions}"
    assert actions.index(Action.BUY) < actions.index(Action.SELL)


def test_no_buy_while_flat_stays_flat():
    bars = _make_bars([100.0] * 20)
    strategy = MovingAverageCrossoverStrategy(fast_period=3, slow_period=5)
    actions = _walk(strategy, bars)
    assert all(a == Action.HOLD for a in actions)


def test_insufficient_bars_returns_hold():
    bars = _make_bars([100.0, 101.0, 102.0])
    strategy = MovingAverageCrossoverStrategy(fast_period=3, slow_period=5)
    signal = strategy.generate_signal("TEST", bars, has_position=False)
    assert signal.action == Action.HOLD


def test_fast_period_must_be_less_than_slow_period():
    with pytest.raises(ValueError):
        MovingAverageCrossoverStrategy(fast_period=50, slow_period=20)


def test_buy_signal_sets_stop_loss():
    flat = [100.0] * 6
    uptrend = [100.0 + i * 3 for i in range(1, 16)]
    bars = _make_bars(flat + uptrend)

    strategy = MovingAverageCrossoverStrategy(fast_period=3, slow_period=5, max_loss_pct=0.05)
    has_position = False
    for i in range(strategy.required_bars(), len(bars)):
        window = bars.iloc[: i + 1]
        signal = strategy.generate_signal("TEST", window, has_position)
        if signal.action == Action.BUY:
            assert signal.stop_loss_price == round(signal.price * 0.95, 2)
            return
    pytest.fail("expected a BUY signal in this series")


def test_trend_filter_blocks_golden_cross_below_long_sma():
    # Price is well below a rising 10-period trend SMA when the golden cross
    # fires, so the BUY must be blocked even though the cross itself is real.
    downtrend = [200.0 - i * 5 for i in range(12)]        # 200 -> 145, seeds a high SMA10
    flat = [50.0] * 6
    uptrend = [50.0 + i * 3 for i in range(1, 16)]          # crosses, but still far below SMA10
    bars = _make_bars(downtrend + flat + uptrend)

    strategy = MovingAverageCrossoverStrategy(fast_period=3, slow_period=5, trend_filter_period=10)
    actions = _walk(strategy, bars)

    assert Action.BUY not in actions, f"expected the trend filter to block every BUY, got {actions}"


def test_trend_filter_allows_golden_cross_above_long_sma():
    flat = [100.0] * 6
    uptrend = [100.0 + i * 3 for i in range(1, 16)]
    bars = _make_bars(flat + uptrend)

    strategy = MovingAverageCrossoverStrategy(fast_period=3, slow_period=5, trend_filter_period=5)
    actions = _walk(strategy, bars)

    assert Action.BUY in actions


def test_volatility_filter_blocks_golden_cross_when_too_choppy():
    # Sawtooth seed keeps daily returns large and noisy, then a golden cross
    # is forced with a single-bar pop - volatility should still be well
    # above the cap from the sawtooth history.
    choppy = [100.0, 130.0, 90.0, 125.0, 95.0, 128.0]
    flat = [100.0] * 4
    bars = _make_bars(choppy + flat + [115.0])

    strategy = MovingAverageCrossoverStrategy(fast_period=3, slow_period=5, max_entry_vol_pct=1.0, entry_vol_period=5)
    signal = strategy.generate_signal("TEST", bars, has_position=False)

    assert signal.action == Action.HOLD
    assert "volatility" in signal.reason


def test_volatility_filter_allows_golden_cross_when_calm():
    flat = [100.0] * 6
    uptrend = [100.0 + i * 3 for i in range(1, 16)]
    bars = _make_bars(flat + uptrend)

    strategy = MovingAverageCrossoverStrategy(fast_period=3, slow_period=5, max_entry_vol_pct=50.0, entry_vol_period=5)
    actions = _walk(strategy, bars)

    assert Action.BUY in actions


def test_filters_disabled_by_default_matches_original_behavior():
    strategy = MovingAverageCrossoverStrategy(fast_period=3, slow_period=5)
    assert strategy.trend_filter_period is None
    assert strategy.max_entry_vol_pct is None
    assert strategy.required_bars() == 6
