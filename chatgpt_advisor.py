"""
chatgpt_advisor.py
Sends RSI data for a stock to ChatGPT and receives a trading decision:
  BUY, SELL, or DO NOTHING.
"""

import os
import json

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

VALID_ACTIONS = {"BUY", "SELL", "DO NOTHING"}

SYSTEM_PROMPT = (
    "You are a world expert stock trader. "
    "You will be given the ticker symbol of a stock and its current RSI (Relative Strength Index) value. "
    "Based solely on the RSI value and your expert trading knowledge, you must decide whether to BUY, SELL, or DO NOTHING. "
    "Respond with ONLY one of these three exact strings — no punctuation, no explanation:\n"
    "BUY\n"
    "SELL\n"
    "DO NOTHING"
)


def get_trading_advice(symbol: str, rsi: float, model: str = "gpt-4o") -> str:
    """
    Ask ChatGPT for a BUY / SELL / DO NOTHING decision.

    Parameters
    ----------
    symbol : str
        Stock ticker, e.g. "AAPL".
    rsi : float
        Current RSI value (0–100).
    model : str
        OpenAI model to use.

    Returns
    -------
    str
        One of "BUY", "SELL", or "DO NOTHING".

    Raises
    ------
    ValueError
        If the model returns an unexpected response.
    """
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    user_message = (
        f"Stock ticker: {symbol}\n"
        f"Current RSI value: {rsi}\n\n"
        "Based on this RSI value, what is your trading decision? "
        "Reply with only: BUY, SELL, or DO NOTHING."
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0.0,
        max_tokens=10,
    )

    raw = response.choices[0].message.content.strip().upper()

    # Normalize slight variations (e.g., "DO_NOTHING", "do nothing")
    if raw == "DO_NOTHING" or raw == "DO-NOTHING":
        raw = "DO NOTHING"

    if raw not in VALID_ACTIONS:
        raise ValueError(
            f"Unexpected response from ChatGPT for {symbol} (RSI={rsi}): '{raw}'. "
            f"Expected one of {VALID_ACTIONS}."
        )

    return raw
