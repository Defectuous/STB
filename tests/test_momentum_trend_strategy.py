"""
tests/test_momentum_trend_strategy.py

Covers MomentumTrendStrategy: the momentum-breakout entry (gated on trend
and volatility) and the rolling trailing-stop exit, mirroring the walk
pattern used in tests/test_mac_strategy.py.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy.base import Action
from strategy.momentum_trend_strategy import MomentumTrendStrategy


def _make_bars(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=len(closes), freq="D")
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


def _walk(strategy: MomentumTrendStrategy, bars: pd.DataFrame) -> list[Action]:
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


def _strategy(**overrides) -> MomentumTrendStrategy:
    params = dict(trend_filter_period=20, entry_vol_period=10, momentum_period=10,
                  min_momentum_pct=15.0, trailing_period=10, trailing_stop_pct=0.10, max_loss_pct=0.08)
    params.update(overrides)
    return MomentumTrendStrategy(**params)


def test_insufficient_bars_returns_hold():
    bars = _make_bars([10.0, 10.1, 10.2])
    strategy = _strategy()
    signal = strategy.generate_signal("TEST", bars, has_position=False)
    assert signal.action == Action.HOLD


def test_buy_fires_on_fresh_momentum_breakout_above_trend():
    # Flat base seeds a low trend SMA, then a sustained ramp both clears the
    # 15% momentum threshold and stays above the trend SMA.
    flat = [10.0] * 25
    ramp = [10.0 * (1 + 0.02) ** i for i in range(1, 15)]  # ~2%/day compounding
    bars = _make_bars(flat + ramp)

    strategy = _strategy(max_entry_vol_pct=50.0)  # vol filter not under test here
    actions = _walk(strategy, bars)

    assert Action.BUY in actions


def test_no_buy_when_below_trend_sma():
    # A steady downtrend never gets price above its own long SMA, so even if
    # a brief pop cleared the momentum threshold, the trend filter blocks it.
    downtrend = [20.0 - i * 0.3 for i in range(30)]
    bars = _make_bars(downtrend)

    strategy = _strategy(max_entry_vol_pct=50.0)
    actions = _walk(strategy, bars)

    assert Action.BUY not in actions


def test_no_buy_when_volatility_too_high():
    choppy = [10.0, 13.0, 9.0, 12.5, 9.5, 12.8, 9.8, 13.2, 9.2, 12.0] * 3
    bars = _make_bars(choppy)

    strategy = _strategy(max_entry_vol_pct=1.0)
    actions = _walk(strategy, bars)

    assert Action.BUY not in actions


def test_buy_signal_sets_stop_loss():
    flat = [10.0] * 25
    ramp = [10.0 * (1 + 0.02) ** i for i in range(1, 15)]
    bars = _make_bars(flat + ramp)

    strategy = _strategy(max_entry_vol_pct=50.0)
    has_position = False
    for i in range(strategy.required_bars(), len(bars)):
        window = bars.iloc[: i + 1]
        signal = strategy.generate_signal("TEST", window, has_position)
        if signal.action == Action.BUY:
            assert signal.stop_loss_price == round(signal.price * (1 - strategy.max_loss_pct), 2)
            return
    pytest.fail("expected a BUY signal in this series")


def test_trailing_stop_exits_after_pullback_from_peak():
    flat = [10.0] * 25
    ramp = [10.0 * (1 + 0.02) ** i for i in range(1, 15)]      # triggers entry
    pullback = [ramp[-1] * (1 - 0.02) ** i for i in range(1, 15)]  # steady decline off the peak
    bars = _make_bars(flat + ramp + pullback)

    strategy = _strategy(max_entry_vol_pct=50.0)
    actions = _walk(strategy, bars)

    assert Action.BUY in actions
    assert Action.SELL in actions
    assert actions.index(Action.BUY) < actions.index(Action.SELL)


def test_no_sell_while_price_holds_above_trailing_stop():
    flat = [10.0] * 25
    ramp = [10.0 * (1 + 0.02) ** i for i in range(1, 15)]
    still_up = [ramp[-1] * (1 + 0.001) ** i for i in range(1, 10)]  # keeps grinding higher
    bars = _make_bars(flat + ramp + still_up)

    strategy = _strategy(max_entry_vol_pct=50.0)
    actions = _walk(strategy, bars)

    assert Action.BUY in actions
    assert Action.SELL not in actions


def test_no_double_buy_while_momentum_stays_above_threshold():
    # Once triggered, has_position=True should suppress re-entry signals
    # even while momentum remains above the threshold on later bars.
    flat = [10.0] * 25
    ramp = [10.0 * (1 + 0.02) ** i for i in range(1, 15)]
    bars = _make_bars(flat + ramp)

    strategy = _strategy(max_entry_vol_pct=50.0)
    actions = _walk(strategy, bars)

    assert actions.count(Action.BUY) == 1
