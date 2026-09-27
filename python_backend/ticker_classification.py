"""
ticker_classification.py

GICS-based company classification for stock tickers.

Fetches exchange + sector/industry from yfinance, generates concise display
tags via a cheap LLM, and caches in the DB with a TTL.

Mirrors the pattern of ticker_colors.py.

Public API:
  init()                  — create DB table
  get_or_fetch(ticker)    — returns classification dict, refreshing if stale
  format_eyebrow(cls)     — "NASDAQ · Semiconductors · AI Infrastructure"
"""

import re
from datetime import datetime, timedelta

import database
import utils

TTL_DAYS = 90

EXCHANGE_MAP = {
    "NMS": "NASDAQ",
    "NGM": "NASDAQ",
    "NNM": "NASDAQ",
    "NYQ": "NYSE",
    "NYS": "NYSE",
    "PCX": "NYSE Arca",
    "ASE": "NYSE American",
    "BTS": "BATS",
    "CME": "CME",
}

EYEBROW_PROMPT = """You are writing a short eyebrow label for a stock ticker page on a financial news platform.

Company: {long_name} ({ticker})
Exchange: {exchange}
GICS Sector: {sector}
GICS Industry: {industry}

Write 2–3 short tags that describe this company, separated by " · ".
Rules:
- Lead with the most specific industry descriptor
- Additional tags should capture what makes this company interesting to investors right now
- Focus on the primary investment narrative, not legacy product lines if they aren't important anymore
- Each tag is 1–3 words, title case
- No ticker symbols, no exchange name, no fluff
- Reply with ONLY the tags, nothing else

Example output (Coca-Cola company): Beverages · Consumer Staples · Defensive Income"""


def init() -> None:
    database.init_ticker_classification_table()


def get_or_fetch(ticker: str) -> dict:
    """
    Return classification for the ticker. Uses the cached DB value if fresh
    (within TTL), otherwise re-fetches from yfinance and regenerates tags.
    Always returns a dict — falls back to minimal defaults on errors.
    """
    cached = database.get_ticker_classification(ticker)
    if cached and _is_fresh(cached.get("fetched_at")):
        return cached

    try:
        raw = _fetch_yfinance(ticker)
    except Exception as e:
        print(f"[ticker_classification] yfinance fetch failed for {ticker}: {e}")
        raw = {}

    tags = _generate_tags(ticker, raw) if raw else None

    data = {
        "long_name": raw.get("long_name", ticker),
        "exchange": raw.get("exchange", ""),
        "gics_sector": raw.get("gics_sector", ""),
        "gics_industry": raw.get("gics_industry", ""),
        "display_tags": tags or raw.get("gics_industry", ""),
    }
    database.save_ticker_classification(ticker, data)
    return {"ticker": ticker, **data}


def format_eyebrow(cls: dict) -> str:
    """Build the hero eyebrow string: 'NASDAQ · Semiconductors · AI Infrastructure'"""
    parts = [p for p in [cls.get("exchange"), cls.get("display_tags")] if p]
    return " · ".join(parts) if parts else ""


# ── Internal ──────────────────────────────────────────────────────────────────


def _is_fresh(fetched_at) -> bool:
    if not fetched_at:
        return False
    try:
        if isinstance(fetched_at, str):
            fetched_at = datetime.fromisoformat(fetched_at)
        return datetime.utcnow() - fetched_at.replace(tzinfo=None) < timedelta(days=TTL_DAYS)
    except (ValueError, TypeError):
        return False


def _fetch_yfinance(ticker: str) -> dict:
    import yfinance as yf

    info = yf.Ticker(utils.to_yfinance_ticker(ticker)).info
    raw_exchange = info.get("exchange", "")
    return {
        "long_name": info.get("longName", ticker),
        "exchange": EXCHANGE_MAP.get(raw_exchange, raw_exchange),
        "gics_sector": info.get("sector", ""),
        "gics_industry": info.get("industry", ""),
    }


def _generate_tags(ticker: str, raw: dict) -> str | None:
    from llms import LLMProviderManager

    llm = LLMProviderManager()
    prompt = EYEBROW_PROMPT.format(
        ticker=ticker,
        long_name=raw.get("long_name", ticker),
        exchange=raw.get("exchange", ""),
        sector=raw.get("gics_sector", ""),
        industry=raw.get("gics_industry", ""),
    )
    try:
        result, _ = llm.call(
            prompt, tier="standard", max_tier="premium", reasoning=False, max_tokens=200
        )
        if result:
            return re.sub(r"[\"\'\n]", "", result).strip()
    except Exception as e:
        print(f"[ticker_classification] LLM tag generation failed for {ticker}: {e}")
    return None
