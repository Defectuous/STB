"""
config_loader.py

Loads config.json, resolves the configured strategy name against
strategy.STRATEGY_REGISTRY, and instantiates it with its params.
"""

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from strategy import STRATEGY_REGISTRY, Strategy


@dataclass
class UniverseConfig:
    mode: str  # "momentum_top_n" is the only dynamic mode; anything else is ignored
    top_n: int
    max_price: float
    min_price: float
    min_avg_volume: float
    lookback_days: int
    exchanges: tuple[str, ...]
    marginable_only: bool


@dataclass
class Config:
    paper_trading: bool
    poll_interval_seconds: int
    log_file: str
    state_file: str
    db_file: str
    symbols: list[str]
    strategy: Strategy
    universe: UniverseConfig | None
    position_size_usd: float | None
    raw: dict


def _load_universe_config(raw: dict) -> UniverseConfig | None:
    universe_cfg = raw.get("universe")
    if not universe_cfg:
        return None
    return UniverseConfig(
        mode=universe_cfg.get("mode", "momentum_top_n"),
        top_n=universe_cfg.get("top_n", 100),
        max_price=universe_cfg.get("max_price", 30.00),
        min_price=universe_cfg.get("min_price", 1.00),
        min_avg_volume=universe_cfg.get("min_avg_volume", 1_000_000),
        lookback_days=universe_cfg.get("lookback_days", 30),
        exchanges=tuple(universe_cfg.get("exchanges", ["NASDAQ", "NYSE", "AMEX"])),
        marginable_only=universe_cfg.get("marginable_only", True),
    )


def load_config(path: str) -> Config:
    config_path = Path(path)
    if not config_path.exists():
        print(f"Config file not found: {config_path.resolve()}", file=sys.stderr)
        sys.exit(1)

    with open(config_path, "r") as f:
        raw = json.load(f)

    strategy_cfg = raw["strategy"]
    strategy_name = strategy_cfg["name"]
    strategy_params = strategy_cfg.get("params", {})

    if strategy_name not in STRATEGY_REGISTRY:
        available = ", ".join(STRATEGY_REGISTRY)
        print(f"Unknown strategy '{strategy_name}'. Available: {available}", file=sys.stderr)
        sys.exit(1)

    universe = _load_universe_config(raw)

    # A dynamic universe supplies its own symbols at runtime (see
    # engine.py's _refresh_universe), so the static list is only required
    # when there's no dynamic universe configured.
    symbols = strategy_params.get("symbols") or []
    if universe is None and not symbols:
        print("strategy.params.symbols must be a non-empty list when no 'universe' is configured", file=sys.stderr)
        sys.exit(1)
    strategy_params = {**strategy_params, "symbols": symbols}

    strategy = STRATEGY_REGISTRY[strategy_name](**strategy_params)

    return Config(
        paper_trading=raw.get("paper_trading", True),
        poll_interval_seconds=raw.get("poll_interval_seconds", 300),
        log_file=raw.get("log_file", "stb.log"),
        state_file=raw.get("state_file", "state.json"),
        db_file=raw.get("db_file", "stb_data.sqlite3"),
        symbols=symbols,
        strategy=strategy,
        universe=universe,
        position_size_usd=raw.get("position_size_usd"),
        raw=raw,
    )
