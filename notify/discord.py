"""
notify/discord.py

Sends trade notifications to a Discord channel via webhook.
The webhook URL is read from the DISCORD_WEBHOOK_URL environment variable.
If unset, notifications are silently skipped.

Generalized from OLD/discord_notify.py's RSI-specific notify_buy/notify_sell
into a single notify_signal() that takes any strategy's Signal.
"""

import logging
import os

import requests

from strategy.base import Action, Signal

log = logging.getLogger(__name__)

_COLOUR = {
    Action.BUY: 0x2ECC71,   # green
    Action.SELL: 0xE74C3C,  # red
}


def _webhook_url() -> str | None:
    return os.getenv("DISCORD_WEBHOOK_URL") or None


def notify_signal(signal: Signal, order_id: str, qty: float, paper: bool = True) -> None:
    """Send a BUY/SELL notification to Discord. No-op for HOLD or if webhook unset."""
    if signal.action == Action.HOLD:
        return

    url = _webhook_url()
    if not url:
        return

    mode = "📄 PAPER" if paper else "💵 LIVE"
    emoji = "🟢" if signal.action == Action.BUY else "🔴"

    fields = [
        {"name": "Ticker", "value": f"`{signal.symbol}`", "inline": True},
        {"name": "Price", "value": f"`${signal.price:,.2f}`", "inline": True},
        {"name": "Qty", "value": f"`{qty:.6f}`", "inline": True},
    ]
    if signal.stop_loss_price is not None:
        fields.append({"name": "Stop Loss", "value": f"`${signal.stop_loss_price:,.2f}`", "inline": True})
    fields.append({"name": "Reason", "value": signal.reason, "inline": False})
    fields.append({"name": "Order ID", "value": f"`{order_id}`", "inline": False})

    _send(
        url,
        title=f"{emoji} {signal.action.value} — {signal.symbol}  {mode}",
        fields=fields,
        colour=_COLOUR[signal.action],
    )


def notify_order_failure(signal: Signal, error: Exception, paper: bool = True) -> None:
    """Alert Discord when order submission fails. No-op if webhook unset."""
    url = _webhook_url()
    if not url:
        return

    mode = "📄 PAPER" if paper else "💵 LIVE"

    fields = [
        {"name": "Ticker", "value": f"`{signal.symbol}`", "inline": True},
        {"name": "Price", "value": f"`${signal.price:,.2f}`", "inline": True},
    ]
    if signal.stop_loss_price is not None:
        fields.append({"name": "Stop Loss", "value": f"`${signal.stop_loss_price:,.2f}`", "inline": True})
    fields.append({"name": "Reason", "value": signal.reason, "inline": False})
    fields.append({"name": "Error", "value": f"```{str(error)[:500]}```", "inline": False})

    _send(
        url,
        title=f"⚠️ {signal.action.value} FAILED — {signal.symbol}  {mode}",
        fields=fields,
        colour=0xF39C12,
    )


def _send(url: str, title: str, fields: list[dict], colour: int) -> None:
    payload = {
        "embeds": [
            {
                "title": title,
                "color": colour,
                "fields": fields,
                "footer": {"text": "Simple Trading Bot"},
            }
        ]
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        log.warning("Discord notification failed: %s", exc)
