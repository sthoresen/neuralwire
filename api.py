"""FastAPI bridge — wraps python_backend without modifying it."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python_backend"))

from db_connection import get_conn  # noqa: E402 — must come after sys.path.insert

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

import database
import market_data
import ticker_colors as tc
import ticker_classification as tclass

# ── Init ───────────────────────────────────────────────────────────────────
database.init_db()
database.init_ticker_artefacts_table()
database.migrate_add_earnings_tables()
tc.init()
tclass.init()

app = FastAPI(title="Pulse API", version="1.0.0")

_frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:3000")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ────────────────────────────────────────────────────────────────
def _available_tickers() -> list[str]:
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT DISTINCT ticker FROM ticker_artefacts
            WHERE artefact_type = 'header_description'
            ORDER BY ticker
        """)
        rows = c.fetchall()
        conn.close()
        return [r[0] for r in rows] if rows else ["NVDA"]
    except Exception:
        return ["NVDA"]


# ── Endpoints ──────────────────────────────────────────────────────────────

@app.get("/tickers")
def list_tickers():
    """Return all tickers that have a header_description artefact."""
    return {"tickers": _available_tickers()}


@app.get("/ticker/{ticker}/header")
def ticker_header(ticker: str):
    """Return ticker classification, accent color, and header description."""
    ticker = ticker.upper()
    cls = tclass.get_or_fetch(ticker)
    accent = tc.resolve(ticker)
    accent_light = tc.darken_hex(accent)
    artefact = database.get_ticker_artefact(ticker, "header_description")
    description = (
        artefact["content"] if artefact
        else f"{cls.get('long_name', ticker)} is a publicly traded company."
    )
    return {
        "ticker": ticker,
        "long_name": cls.get("long_name", ticker),
        "eyebrow": tclass.format_eyebrow(cls),
        "description": description,
        "accent": accent,
        "accent_light": accent_light,
        "accent_dim": tc.hex_to_rgba(accent, 0.10),
        "accent_glow": tc.hex_to_rgba(accent, 0.05),
    }


@app.get("/ticker/{ticker}/prices")
def ticker_prices(ticker: str):
    """Return historical daily close prices."""
    ticker = ticker.upper()
    rows = market_data.get_prices(ticker)
    return {"ticker": ticker, "prices": rows or []}


@app.get("/ticker/{ticker}/intraday")
def ticker_intraday(ticker: str):
    """Return intraday (1-min) prices."""
    ticker = ticker.upper()
    rows = market_data.get_intraday(ticker)
    return {"ticker": ticker, "intraday": rows or []}


@app.get("/ticker/{ticker}/articles")
def ticker_articles(
    ticker: str,
    lookback_days: int = Query(30, ge=1, le=365),
    limit: int = Query(60, ge=1, le=200),
):
    """Return recent news articles with AI analysis scores."""
    ticker = ticker.upper()
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT ar.impact_headline, a.headline, a.url, a.published_at, a.provider,
               ar.ai_summary, ar.importance_score, ar.relevancy_score, ar.breaking_news_score
        FROM analysis_runs ar
        JOIN articles a ON ar.article_id = a.id
        WHERE ar.ticker = %s AND a.published_at >= NOW() - (%s * INTERVAL '1 day')
        ORDER BY a.published_at DESC
        LIMIT %s
    """, (ticker, lookback_days, limit))
    rows = c.fetchall()
    conn.close()
    cols = ["impact_headline", "headline", "url", "published_at", "provider",
            "ai_summary", "importance_score", "relevancy_score", "breaking_news_score"]
    return {"ticker": ticker, "articles": [dict(zip(cols, r)) for r in rows]}


@app.get("/ticker/{ticker}/coverage")
def ticker_coverage(
    ticker: str,
    min_relevance: int = Query(50, ge=0, le=100),
    min_breaking: int = Query(0, ge=0, le=100),
    min_importance: int = Query(0, ge=0, le=100),
    limit: int = Query(200, ge=1, le=500),
):
    """Return filtered articles for the Coverage page."""
    ticker = ticker.upper()
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT ar.impact_headline, a.headline, a.url, a.published_at, a.provider,
               ar.ai_summary, ar.importance_score, ar.relevancy_score, ar.breaking_news_score,
               ar.ticker
        FROM analysis_runs ar
        JOIN articles a ON ar.article_id = a.id
        WHERE ar.ticker = %s
          AND ar.relevancy_score     >= %s
          AND ar.breaking_news_score >= %s
          AND ar.importance_score    >= %s
        ORDER BY a.published_at DESC
        LIMIT %s
    """, (ticker, min_relevance, min_breaking, min_importance, limit))
    rows = c.fetchall()
    conn.close()
    cols = ["impact_headline", "headline", "url", "published_at", "provider",
            "ai_summary", "importance_score", "relevancy_score", "breaking_news_score", "ticker"]
    # Deduplicate by URL
    seen: set[str] = set()
    articles = []
    for r in rows:
        d = dict(zip(cols, r))
        if d["url"] not in seen:
            seen.add(d["url"])
            articles.append(d)
    return {"ticker": ticker, "articles": articles}


@app.get("/ticker/{ticker}/focal-points")
def ticker_focal_points(ticker: str):
    """Return the latest focal points artefact."""
    ticker = ticker.upper()
    artefact = database.get_focal_points(ticker)
    if not artefact:
        return {"ticker": ticker, "content": None, "generated_at": None, "model_name": None}
    return {
        "ticker": ticker,
        "content": artefact["content"],
        "generated_at": artefact.get("generated_at"),
        "model_name": artefact.get("model_name"),
    }


@app.get("/ticker/{ticker}/monthly-news-flow")
def ticker_monthly_news_flow(ticker: str):
    """Return the latest monthly news flow artefact."""
    ticker = ticker.upper()
    artefact = database.get_monthly_news_flow(ticker)
    if not artefact:
        return {"ticker": ticker, "content": None, "generated_at": None, "model_name": None}
    return {
        "ticker": ticker,
        "content": artefact["content"],
        "generated_at": artefact.get("generated_at"),
        "model_name": artefact.get("model_name"),
    }


@app.get("/ticker/{ticker}/events")
def ticker_events(ticker: str):
    """Return the event timeline for a ticker."""
    ticker = ticker.upper()
    events = database.get_ticker_events(ticker)
    return {"ticker": ticker, "events": events or []}
