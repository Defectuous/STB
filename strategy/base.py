"""
strategy/base.py

Defines the interface every strategy must implement. The engine only ever
talks to strategies through this contract, so new strategies can be dropped
in without touching engine.py, main.py, or the broker.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

import pandas as pd
from alpaca.data.timeframe import TimeFrame


class Action(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class Signal:
    action: Action
    symbol: str
    reason: str
    price: float
    stop_loss_price: float | None = None
    allocation_pct: float | None = None


class Strategy(ABC):
    """Base class for all trading strategies."""

    name: str = "base"

    @abstractmethod
    def required_bars(self) -> int:
        """Minimum number of bars needed before a signal can be computed."""
        raise NotImplementedError

    @abstractmethod
    def timeframe(self) -> TimeFrame:
        """The bar timeframe this strategy operates on."""
        raise NotImplementedError

    @abstractmethod
    def generate_signal(self, symbol: str, bars: pd.DataFrame, has_position: bool) -> Signal:
        """
        Evaluate the most recent closed bar and return a Signal.

        `bars` is ordered oldest -> newest and must contain a "close" column.
        `has_position` tells the strategy whether the account currently
        holds a position in `symbol`, so it doesn't emit a BUY while already
        long or a SELL while flat.
        """
        raise NotImplementedError
