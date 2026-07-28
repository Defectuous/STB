"""
state.py

Small JSON-backed state file tracking, per symbol, the timestamp of the last
bar the engine acted on. Since this is a daily-bar strategy polled more
often than once a day, this stops the engine from re-evaluating (and
re-notifying) the same closed bar on every poll — it only acts again once a
new bar has closed.
"""

import json
from pathlib import Path


class TradeState:
    def __init__(self, path: str):
        self.path = Path(path)
        self._data: dict = json.loads(self.path.read_text()) if self.path.exists() else {}

    def last_bar_ts(self, symbol: str) -> str | None:
        return self._data.get(symbol, {}).get("last_bar_ts")

    def record(self, symbol: str, bar_ts: str, action: str) -> None:
        self._data[symbol] = {"last_bar_ts": bar_ts, "last_action": action}
        self.path.write_text(json.dumps(self._data, indent=2))
