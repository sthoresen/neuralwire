"""FastAPI bridge — wraps python_backend without modifying it."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python_backend"))

from db_connection import get_conn  # noqa: E402 — must come after sys.path.insert

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timezone

import database
import market_data
import ticker_colors as tc
import ticker_classification as tclass
import utils

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
    return [utils.to_display_ticker(t) for t in database.get_active_tickers()]


# ── Endpoints ──────────────────────────────────────────────────────────────

@app.get("/tickers")
def list_tickers():
    """Return all tickers that have a header_description artefact."""
    return {"tickers": _available_tickers()}


@app.get("/ticker/{ticker}/header")
def ticker_header(ticker: str):
    """Return ticker classification, accent color, and header description."""
    ticker = utils.to_db_ticker(ticker.upper())
    cls = tclass.get_or_fetch(ticker)
    accent = tc.resolve(ticker)
    accent_light = tc.darken_hex(accent)
    artefact = database.get_ticker_artefact(ticker, "header_description")
    description = (
        artefact["content"] if artefact
        else f"{cls.get('long_name', ticker)} is a publicly traded company."
    )
    return {
        "ticker": utils.to_display_ticker(ticker),
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
    ticker = utils.to_db_ticker(ticker.upper())
    rows = market_data.get_prices(ticker)
    return {"ticker": utils.to_display_ticker(ticker), "prices": rows or []}


def _market_is_open() -> bool:
    """True if NYSE is currently open (Mon–Fri 13:30–21:00 UTC, DST-agnostic)."""
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 13 * 60 + 30 <= minutes < 21 * 60


def _intraday_is_stale(ticker: str) -> bool:
    """True if intraday data is missing or older than 2 minutes."""
    latest = market_data.get_latest_intraday_timestamp(ticker)
    if not latest:
        return True
    latest_dt = datetime.fromisoformat(latest.replace(" ", "T")).replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - latest_dt).total_seconds()
    return age_seconds > 120


def _refresh_intraday(db_ticker: str):
    """Background task: fetch latest minute bars from Alpaca and upsert into DB."""
    try:
        from datetime import date, timedelta, datetime as dt
        import alpaca

        latest = market_data.get_latest_intraday_timestamp(db_ticker)
        if latest:
            start = str((dt.fromisoformat(latest.replace(" ", "T")) + timedelta(minutes=1)).date())
        else:
            start = str(date.today())
        end = str(date.today())

        bars = alpaca.get_bars(utils.to_alpaca_ticker(db_ticker), "1Min", start, end)
        if not bars:
            return

        rows = alpaca.bars_to_intraday_rows(db_ticker, bars)
        market_data.bulk_insert_intraday(rows)
        print(f"[intraday refresh] {db_ticker}: upserted {len(rows)} bars", flush=True)
    except Exception as e:
        print(f"[intraday refresh] {db_ticker} ERROR: {e}", flush=True)


@app.get("/ticker/{ticker}/intraday")
def ticker_intraday(ticker: str, background_tasks: BackgroundTasks):
    """Return intraday (1-min) prices. Triggers a background refresh if market is open and data is stale."""
    ticker = utils.to_db_ticker(ticker.upper())
    if _market_is_open() and _intraday_is_stale(ticker):
        background_tasks.add_task(_refresh_intraday, ticker)
    rows = market_data.get_intraday(ticker)
    return {"ticker": utils.to_display_ticker(ticker), "intraday": rows or [], "market_open": _market_is_open()}


@app.get("/ticker/{ticker}/articles")
def ticker_articles(
    ticker: str,
    lookback_days: int = Query(30, ge=1, le=365),
    limit: int = Query(60, ge=1, le=200),
):
    """Return recent news articles with AI analysis scores."""
    ticker = utils.to_db_ticker(ticker.upper())
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
    return {"ticker": utils.to_display_ticker(ticker), "articles": [dict(zip(cols, r)) for r in rows]}


@app.get("/ticker/{ticker}/coverage")
def ticker_coverage(
    ticker: str,
    min_relevance: int = Query(50, ge=0, le=100),
    min_breaking: int = Query(0, ge=0, le=100),
    min_importance: int = Query(0, ge=0, le=100),
    limit: int = Query(200, ge=1, le=500),
):
    """Return filtered articles for the Coverage page."""
    ticker = utils.to_db_ticker(ticker.upper())
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
    return {"ticker": utils.to_display_ticker(ticker), "articles": articles}


@app.get("/ticker/{ticker}/focal-points")
def ticker_focal_points(ticker: str):
    """Return the latest focal points artefact."""
    ticker = utils.to_db_ticker(ticker.upper())
    artefact = database.get_focal_points(ticker)
    if not artefact:
        return {"ticker": utils.to_display_ticker(ticker), "content": None, "generated_at": None, "model_name": None}
    return {
        "ticker": utils.to_display_ticker(ticker),
        "content": artefact["content"],
        "generated_at": artefact.get("generated_at"),
        "model_name": artefact.get("model_name"),
    }


@app.get("/ticker/{ticker}/monthly-news-flow")
def ticker_monthly_news_flow(ticker: str):
    """Return the latest monthly news flow artefact."""
    ticker = utils.to_db_ticker(ticker.upper())
    artefact = database.get_monthly_news_flow(ticker)
    if not artefact:
        return {"ticker": utils.to_display_ticker(ticker), "content": None, "generated_at": None, "model_name": None}
    return {
        "ticker": utils.to_display_ticker(ticker),
        "content": artefact["content"],
        "generated_at": artefact.get("generated_at"),
        "model_name": artefact.get("model_name"),
    }


@app.get("/ticker/{ticker}/events")
def ticker_events(ticker: str):
    """Return the event timeline for a ticker."""
    ticker = utils.to_db_ticker(ticker.upper())
    events = database.get_ticker_events(ticker)
    return {"ticker": utils.to_display_ticker(ticker), "events": events or []}
