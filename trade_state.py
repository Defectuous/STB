"""
trade_state.py
Tracks daily trading state (buys used, sells used, sold-today flag) in a
local JSON file.  State automatically resets when a new calendar day starts.
"""

import json
import os
from datetime import date

STATE_FILE = "daily_trade_state.json"

_DEFAULT_STATE = {
    "date": "",          # ISO date string, e.g. "2026-03-06"
    "buys_today": 0,     # number of BUY orders placed today
    "sells_today": 0,    # number of SELL orders placed today
    "sold_today": False, # True once any SELL has been executed today
}


def _today() -> str:
    return date.today().isoformat()


def _load() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            try:
                state = json.load(f)
            except json.JSONDecodeError:
                state = {}
    else:
        state = {}

    # Reset if it's a new day
    if state.get("date") != _today():
        state = dict(_DEFAULT_STATE)
        state["date"] = _today()
        _save(state)

    return state


def _save(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── Public API ────────────────────────────────────────────────────────────────

def can_buy() -> bool:
    """Return True if a BUY trade is still allowed today."""
    state = _load()
    return state["buys_today"] < 1 and not state["sold_today"]


def can_sell() -> bool:
    """Return True if a SELL trade is still allowed today."""
    state = _load()
    return state["sells_today"] < 1


def record_buy() -> None:
    """Mark that a BUY was executed today."""
    state = _load()
    state["buys_today"] += 1
    _save(state)


def record_sell() -> None:
    """Mark that a SELL was executed today and stop further trading."""
    state = _load()
    state["sells_today"] += 1
    state["sold_today"] = True
    _save(state)


def has_sold_today() -> bool:
    """Return True if a SELL already occurred today (trading halted)."""
    return _load()["sold_today"]


def get_state() -> dict:
    """Return the current daily state dict (useful for logging)."""
    return _load()
