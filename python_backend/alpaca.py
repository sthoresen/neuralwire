"""
alpaca.py — Thin wrapper around Alpaca Market Data REST API v2.

Replaces alpaca-trade-api SDK to avoid websockets dependency conflicts.
Only implements what the project needs: historical daily bars and minute bars.

Docs: https://docs.alpaca.markets/reference/stockbars
"""

import requests
import utils

_BASE = "https://data.alpaca.markets/v2"


def _headers() -> dict:
    return {
        "APCA-API-KEY-ID":     utils.get_env_variable("ALPACA_KEY"),
        "APCA-API-SECRET-KEY": utils.get_env_variable("ALPACA_SECRET"),
    }


def get_bars(ticker: str, timeframe: str, start: str, end: str,
             feed: str = "iex", limit: int = 10000) -> list[dict]:
    """
    Fetch OHLCV bars for a single ticker. Handles pagination automatically.

    timeframe: "1Day" or "1Min"
    Returns a list of dicts with keys: t, o, h, l, c, v, vw, n
    """
    url = f"{_BASE}/stocks/{ticker}/bars"
    params = {
        "timeframe":  timeframe,
        "start":      start,
        "end":        end,
        "feed":       feed,
        "limit":      limit,
        "adjustment": "split",
    }

    bars = []
    while True:
        r = requests.get(url, headers=_headers(), params=params)
        r.raise_for_status()
        data = r.json()
        bars.extend(data.get("bars") or [])
        token = data.get("next_page_token")
        if not token:
            break
        params["page_token"] = token

    return bars


def bars_to_daily_rows(ticker: str, bars: list[dict]) -> list[dict]:
    """Convert raw bar dicts to the format expected by market_data.bulk_insert_prices."""
    rows = []
    for b in bars:
        # Alpaca daily bar timestamp is like "2026-04-09T00:00:00Z"
        date_str = b["t"][:10]
        rows.append({
            "ticker":      ticker,
            "date":        date_str,
            "open":        b.get("o"),
            "high":        b.get("h"),
            "low":         b.get("l"),
            "close":       b.get("c"),
            "volume":      b.get("v"),
            "vwap":        b.get("vw"),
            "trade_count": b.get("n"),
        })
    return rows


def bars_to_intraday_rows(ticker: str, bars: list[dict]) -> list[dict]:
    """Convert raw bar dicts to the format expected by market_data.bulk_insert_intraday."""
    rows = []
    for b in bars:
        rows.append({
            "ticker":      ticker,
            "timestamp":   b["t"],
            "open":        b.get("o"),
            "high":        b.get("h"),
            "low":         b.get("l"),
            "close":       b.get("c"),
            "volume":      b.get("v"),
            "vwap":        b.get("vw"),
            "trade_count": b.get("n"),
        })
    return rows
