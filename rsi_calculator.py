"""
rsi_calculator.py
Calculates the Relative Strength Index (RSI) from a list of closing prices.

Formula:
    RS  = Average Gain / Average Loss  (over `period` bars, Wilder's smoothing)
    RSI = 100 - (100 / (1 + RS))

RSI interpretation (classic):
    < 30  → oversold  (potential BUY)
    > 70  → overbought (potential SELL)
    30-70 → neutral   (DO NOTHING)
"""

import numpy as np


def calculate_rsi(closes: list[float], period: int = 14) -> float:
    """
    Compute RSI for the most-recent bar given a list of daily closing prices.

    Parameters
    ----------
    closes : list[float]
        Closing prices ordered oldest → newest.
        Must contain at least `period + 1` values.
    period : int
        Look-back window (default 14).

    Returns
    -------
    float
        RSI value in the range [0, 100], rounded to 2 decimal places.

    Raises
    ------
    ValueError
        If not enough data is provided.
    """
    if len(closes) < period + 1:
        raise ValueError(
            f"Not enough data to calculate RSI. Need at least {period + 1} "
            f"closing prices, got {len(closes)}."
        )

    closes_arr = np.array(closes, dtype=float)
    deltas = np.diff(closes_arr)

    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # Initial averages (simple mean over first `period` changes)
    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()

    # Wilder's smoothing for the remaining bars
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(rsi, 2)
