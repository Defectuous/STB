"""
strategy package

Every strategy implements the Strategy interface in strategy/base.py and
registers itself in STRATEGY_REGISTRY below, keyed by the name used in
config.json's "strategy.name" field. To add a new strategy: write a new
module implementing Strategy, then add one line here.
"""

from strategy.base import Action, Signal, Strategy
from strategy.mac_strategy import MovingAverageCrossoverStrategy
from strategy.mean_reversion_strategy import MeanReversionStrategy
from strategy.momentum_trend_strategy import MomentumTrendStrategy

STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    MovingAverageCrossoverStrategy.name: MovingAverageCrossoverStrategy,
    MomentumTrendStrategy.name: MomentumTrendStrategy,
    MeanReversionStrategy.name: MeanReversionStrategy,
}

__all__ = ["Action", "Signal", "Strategy", "STRATEGY_REGISTRY"]
