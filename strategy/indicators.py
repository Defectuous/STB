"""
strategy/indicators.py

Small shared indicator helpers used by both strategy/mac_strategy.py and
screener.py, so the crossover math is defined in exactly one place.
"""

import pandas as pd


def sma(closes: pd.Series, period: int) -> pd.Series:
    """Simple moving average of a close-price series."""
    return closes.rolling(period).mean()


def crossed_above(fast_prev: float, slow_prev: float, fast_now: float, slow_now: float) -> bool:
    """True if `fast` moved from at/below `slow` yesterday to strictly above it today."""
    return fast_prev <= slow_prev and fast_now > slow_now


def crossed_below(fast_prev: float, slow_prev: float, fast_now: float, slow_now: float) -> bool:
    """True if `fast` moved from at/above `slow` yesterday to strictly below it today."""
    return fast_prev >= slow_prev and fast_now < slow_now


def pct_distance(a: float, b: float) -> float:
    """Absolute distance between a and b as a percentage of b (0.02 == 2%)."""
    if b == 0:
        return float("inf")
    return abs(a - b) / abs(b)


def realized_vol_pct(closes: pd.Series, period: int) -> pd.Series:
    """Rolling `period`-bar realized volatility of daily returns, as a percentage (3.2 == 3.2%)."""
    return closes.pct_change().rolling(period).std() * 100


def momentum_pct(closes: pd.Series, period: int) -> pd.Series:
    """Trailing `period`-bar price return, as a percentage (15.0 == +15%)."""
    return closes.pct_change(periods=period) * 100


def trailing_stop_price(closes: pd.Series, period: int, stop_pct: float) -> pd.Series:
    """
    Rolling stop level: stop_pct below the highest close in the prior
    `period` bars (today excluded, so today's close can't chase its own tail).
    """
    return closes.shift(1).rolling(period).max() * (1 - stop_pct)


def rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (0-100) of a close-price series, simple-average variant."""
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))
