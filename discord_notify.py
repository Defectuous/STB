"""
discord_notify.py
Sends trade notifications to a Discord channel via webhook.
The webhook URL is read from the DISCORD_WEBHOOK_URL environment variable.
If the variable is not set, notifications are silently skipped.
"""

import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

_COLOUR = {
    "BUY":  0x2ECC71,   # green
    "SELL": 0xE74C3C,   # red
}


def _webhook_url() -> str | None:
    return os.getenv("DISCORD_WEBHOOK_URL") or None


def notify_buy(
    symbol: str,
    rsi: float,
    order_type: str,
    notional: float,
    order_id: str,
    limit_price: float | None = None,
    qty: float | None = None,
    paper: bool = True,
) -> None:
    """Send a BUY notification to Discord."""
    url = _webhook_url()
    if not url:
        return

    mode = "📄 PAPER" if paper else "💵 LIVE"

    fields = [
        {"name": "Ticker",     "value": f"`{symbol}`",                  "inline": True},
        {"name": "RSI",        "value": f"`{rsi:.2f}`",                 "inline": True},
        {"name": "Order Type", "value": f"`{order_type.upper()}`",      "inline": True},
        {"name": "Spend",      "value": f"`${notional:,.2f}`",          "inline": True},
    ]
    if limit_price is not None:
        fields.append({"name": "Limit Price", "value": f"`${limit_price:,.2f}`", "inline": True})
    if qty is not None:
        fields.append({"name": "Qty",         "value": f"`{qty:.6f}`",          "inline": True})
    fields.append({"name": "Order ID", "value": f"`{order_id}`", "inline": False})

    _send(url, title=f"🟢 BUY — {symbol}  {mode}", fields=fields, colour=_COLOUR["BUY"])


def notify_sell(
    symbol: str,
    rsi: float,
    qty: float,
    order_id: str,
    paper: bool = True,
) -> None:
    """Send a SELL notification to Discord."""
    url = _webhook_url()
    if not url:
        return

    mode = "📄 PAPER" if paper else "💵 LIVE"

    fields = [
        {"name": "Ticker",   "value": f"`{symbol}`",      "inline": True},
        {"name": "RSI",      "value": f"`{rsi:.2f}`",     "inline": True},
        {"name": "Qty Sold", "value": f"`{qty:.6f}`",     "inline": True},
        {"name": "Order ID", "value": f"`{order_id}`",    "inline": False},
    ]

    _send(url, title=f"🔴 SELL — {symbol}  {mode}", fields=fields, colour=_COLOUR["SELL"])


def _send(url: str, title: str, fields: list[dict], colour: int) -> None:
    payload = {
        "embeds": [
            {
                "title":  title,
                "color":  colour,
                "fields": fields,
                "footer": {"text": "STB — Stock Trading Bot"},
            }
        ]
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        log.warning("Discord notification failed: %s", exc)
