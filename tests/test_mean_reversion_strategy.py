"""
tests/test_mean_reversion_strategy.py

Covers MeanReversionStrategy: dip-buy entry (RSI oversold, gated on the
trend filter) and the RSI-recovery exit, mirroring the walk pattern used
in tests/test_mac_strategy.py.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy.base import Action
from strategy.mean_reversion_strategy import MeanReversionStrategy


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


def _walk(strategy: MeanReversionStrategy, bars: pd.DataFrame) -> list[Action]:
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


def _strategy(**overrides) -> MeanReversionStrategy:
    # take_profit_pct defaults off here so RSI-only tests aren't affected by
    # the class's live-config default; test_sell_fires_on_profit_target
    # opts it back in explicitly.
    params = dict(trend_filter_period=50, rsi_period=14, oversold_threshold=30.0, exit_rsi_threshold=55.0,
                  take_profit_pct=None, max_loss_pct=0.08)
    params.update(overrides)
    return MeanReversionStrategy(**params)


def _uptrend_then_dip() -> list[float]:
    # A long compounding uptrend (100 bars) puts a wide cushion between price
    # and its own 50-day SMA, so the dip that follows can push RSI(14) into
    # oversold territory well before price itself crosses below the trend SMA.
    uptrend = [10.0 * (1.03 ** i) for i in range(100)]
    dip = [uptrend[-1] * (0.97 ** i) for i in range(1, 16)]
    return uptrend + dip


def test_insufficient_bars_returns_hold():
    bars = _make_bars([10.0, 10.1, 10.2])
    signal = _strategy().generate_signal("TEST", bars, has_position=False)
    assert signal.action == Action.HOLD


def test_buy_fires_on_oversold_dip_within_uptrend():
    bars = _make_bars(_uptrend_then_dip())

    strategy = _strategy()
    actions = _walk(strategy, bars)

    assert Action.BUY in actions


def test_no_buy_when_below_trend_sma():
    downtrend = [20.0 - i * 0.3 for i in range(60)]
    bars = _make_bars(downtrend)

    strategy = _strategy()
    actions = _walk(strategy, bars)

    assert Action.BUY not in actions


def test_buy_signal_sets_stop_loss():
    bars = _make_bars(_uptrend_then_dip())

    strategy = _strategy()
    has_position = False
    for i in range(strategy.required_bars(), len(bars)):
        window = bars.iloc[: i + 1]
        signal = strategy.generate_signal("TEST", window, has_position)
        if signal.action == Action.BUY:
            assert signal.stop_loss_price == round(signal.price * (1 - strategy.max_loss_pct), 2)
            return
    pytest.fail("expected a BUY signal in this series")


def test_sell_fires_once_rsi_recovers():
    dip_close = _uptrend_then_dip()[-1]
    bounce = [dip_close * (1.05 ** i) for i in range(1, 10)]  # sharp recovery pushes RSI back up
    bars = _make_bars(_uptrend_then_dip() + bounce)

    strategy = _strategy()
    actions = _walk(strategy, bars)

    assert Action.BUY in actions
    assert Action.SELL in actions
    assert actions.index(Action.BUY) < actions.index(Action.SELL)


def test_no_sell_while_rsi_stays_below_exit_threshold():
    dip_close = _uptrend_then_dip()[-1]
    flat_after_dip = [dip_close] * 10  # sits flat, never recovers enough to trip the exit RSI
    bars = _make_bars(_uptrend_then_dip() + flat_after_dip)

    strategy = _strategy()
    actions = _walk(strategy, bars)

    assert Action.BUY in actions
    assert Action.SELL not in actions


def test_no_double_buy_while_holding():
    bars = _make_bars(_uptrend_then_dip())

    strategy = _strategy()
    actions = _walk(strategy, bars)

    assert actions.count(Action.BUY) <= 1


def test_sell_fires_on_profit_target_before_rsi_recovers():
    # Entry fires partway down the dip (~137.6, not at the dip's final low
    # of ~118.2), so the bounce needs to climb back past entry+2% (~140.3),
    # which happens around bar +8 here while RSI is still a moderate ~54 -
    # comfortably below the deliberately-high exit_rsi_threshold.
    dip_close = _uptrend_then_dip()[-1]
    gentle_bounce = [dip_close * (1.02 ** i) for i in range(1, 13)]
    bars = _make_bars(_uptrend_then_dip() + gentle_bounce)

    strategy = _strategy(take_profit_pct=0.02, exit_rsi_threshold=95.0)
    has_position = False
    entry_price = None
    for i in range(strategy.required_bars(), len(bars)):
        window = bars.iloc[: i + 1]
        signal = strategy.generate_signal("TEST", window, has_position)
        if signal.action == Action.BUY:
            has_position = True
            entry_price = signal.price
        elif signal.action == Action.SELL:
            assert "profit target" in signal.reason
            assert signal.price >= entry_price * 1.02
            return
    pytest.fail("expected a profit-target SELL in this series")


def test_no_profit_target_sell_before_price_reaches_it():
    dip_close = _uptrend_then_dip()[-1]
    barely_up = [dip_close * 1.005] * 5  # up less than the 2% target, and flat - shouldn't trip either exit
    bars = _make_bars(_uptrend_then_dip() + barely_up)

    strategy = _strategy(take_profit_pct=0.02, exit_rsi_threshold=95.0)
    actions = _walk(strategy, bars)

    assert Action.BUY in actions
    assert Action.SELL not in actions
