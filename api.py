"""FastAPI bridge — wraps python_backend without modifying it."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python_backend"))

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import database
import market_data
import sync_intraday
import ticker_classification as tclass
import ticker_colors as tc
import utils
import x_digest
import x_jev
import x_sources
import x_store
from db_connection import get_conn  # noqa: E402 — must come after sys.path.insert

# ── Init ───────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Create tables and seed lookups once at server startup, not at import,
    so the app can be imported (e.g. by tests) without a database."""
    database.init_db()
    database.init_ticker_artefacts_table()
    database.migrate_add_earnings_tables()
    tc.init()
    tclass.init()
    x_store.init_tables()
    yield


app = FastAPI(title="NeuralWire API", version="1.0.0", lifespan=lifespan)

_frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:3000")
# Optional regex to also allow preview deployments (e.g. Vercel branch previews).
# Off unless set — scope it to your own project, e.g. https://neuralwire-.*\.vercel\.app
_preview_regex = os.environ.get("FRONTEND_PREVIEW_REGEX") or None

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_frontend_url],
    allow_origin_regex=_preview_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ────────────────────────────────────────────────────────────────
def _available_tickers() -> list[str]:
    return [utils.to_display_ticker(t) for t in database.get_active_tickers()]


def known_ticker(ticker: str) -> str:
    """
    Normalise a URL ticker to DB format (brk.b -> BRKB) and reject tickers that are
    not on the watchlist with a 404. Runs before every /ticker/{ticker}/... endpoint,
    so unknown tickers never reach the yfinance lookups or LLM calls behind them.
    """
    db_ticker = utils.to_db_ticker(ticker.upper())
    if db_ticker not in database.get_active_tickers():
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    return db_ticker


# The DB-format ticker, already validated against the watchlist.
KnownTicker = Annotated[str, Depends(known_ticker)]


# ── Endpoints ──────────────────────────────────────────────────────────────


@app.get("/health")
def health() -> dict[str, str]:
    """
    Railway healthcheck: a new deploy only takes traffic once this returns 200.
    Requests are served only after lifespan startup succeeds, so no further checks are needed.
    """
    return {"status": "ok"}


@app.get("/tickers")
def list_tickers():
    """Return all tickers that have a header_description artefact."""
    return {"tickers": _available_tickers()}


@app.get("/ticker/{ticker}/header")
def ticker_header(ticker: KnownTicker):
    """Return ticker classification, accent color, and header description."""
    cls = tclass.get_or_fetch(ticker)
    accent = tc.resolve(ticker)
    accent_light = tc.darken_hex(accent)
    artefact = database.get_ticker_artefact(ticker, "header_description")
    description = (
        artefact["content"]
        if artefact
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
def ticker_prices(ticker: KnownTicker):
    """Return historical daily close prices."""
    rows = market_data.get_prices(ticker)
    return {"ticker": utils.to_display_ticker(ticker), "prices": rows or []}


def _market_is_open() -> bool:
    """True if NYSE is currently open (Mon–Fri 13:30–21:00 UTC, DST-agnostic)."""
    now = datetime.now(UTC)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 13 * 60 + 30 <= minutes < 21 * 60


def _intraday_is_stale(ticker: str) -> bool:
    """True if intraday data is missing or older than 2 minutes."""
    latest = market_data.get_latest_intraday_timestamp(ticker)
    if not latest:
        return True
    latest_dt = datetime.fromisoformat(latest.replace(" ", "T")).replace(tzinfo=UTC)
    age_seconds = (datetime.now(UTC) - latest_dt).total_seconds()
    return age_seconds > 120


def _refresh_intraday(db_ticker: str) -> None:
    """Background task: fetch latest minute bars from Alpaca and upsert into DB."""
    try:
        inserted = sync_intraday.sync_ticker_intraday(db_ticker)
        print(f"[intraday refresh] {db_ticker}: upserted {inserted} bars", flush=True)
    except Exception as e:
        print(f"[intraday refresh] {db_ticker} ERROR: {e}", flush=True)


@app.get("/ticker/{ticker}/intraday")
def ticker_intraday(ticker: KnownTicker, background_tasks: BackgroundTasks):
    """Return intraday (1-min) prices. Triggers a background refresh if market is open and data is stale."""
    from datetime import date, timedelta

    if _market_is_open() and _intraday_is_stale(ticker):
        background_tasks.add_task(_refresh_intraday, ticker)
    window_start = str(date.today() - timedelta(days=7))
    rows = market_data.get_intraday(ticker, start=window_start)
    return {
        "ticker": utils.to_display_ticker(ticker),
        "intraday": rows or [],
        "market_open": _market_is_open(),
    }


@app.get("/ticker/{ticker}/articles")
def ticker_articles(
    ticker: KnownTicker,
    lookback_days: int = Query(30, ge=1, le=365),
    limit: int = Query(60, ge=1, le=200),
):
    """Return recent news articles with AI analysis scores."""
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()
    c.execute(
        """
        SELECT ar.impact_headline, a.headline, a.url, a.published_at, a.provider,
               ar.ai_summary, ar.importance_score, ar.relevancy_score, ar.breaking_news_score
        FROM analysis_runs ar
        JOIN articles a ON ar.article_id = a.id
        WHERE ar.ticker = %s AND a.published_at >= NOW() - (%s * INTERVAL '1 day')
        ORDER BY a.published_at DESC
        LIMIT %s
    """,
        (ticker, lookback_days, limit),
    )
    rows = c.fetchall()
    conn.close()
    return {"ticker": utils.to_display_ticker(ticker), "articles": [dict(r) for r in rows]}


@app.get("/ticker/{ticker}/coverage")
def ticker_coverage(
    ticker: KnownTicker,
    min_relevance: int = Query(50, ge=0, le=100),
    min_breaking: int = Query(0, ge=0, le=100),
    min_importance: int = Query(0, ge=0, le=100),
    limit: int = Query(200, ge=1, le=500),
):
    """Return filtered articles for the Coverage page."""
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()
    c.execute(
        """
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
    """,
        (ticker, min_relevance, min_breaking, min_importance, limit),
    )
    rows = c.fetchall()
    conn.close()
    # Deduplicate by URL
    seen: set[str] = set()
    articles = []
    for r in rows:
        d = dict(r)
        if d["url"] not in seen:
            seen.add(d["url"])
            articles.append(d)
    return {"ticker": utils.to_display_ticker(ticker), "articles": articles}


@app.get("/ticker/{ticker}/focal-points")
def ticker_focal_points(ticker: KnownTicker):
    """Return the latest focal points artefact."""
    artefact = database.get_focal_points(ticker)
    if not artefact:
        return {
            "ticker": utils.to_display_ticker(ticker),
            "content": None,
            "generated_at": None,
            "model_name": None,
        }
    return {
        "ticker": utils.to_display_ticker(ticker),
        "content": artefact["content"],
        "generated_at": artefact.get("generated_at"),
        "model_name": artefact.get("model_name"),
    }


@app.get("/ticker/{ticker}/monthly-news-flow")
def ticker_monthly_news_flow(ticker: KnownTicker):
    """Return the latest monthly news flow artefact."""
    artefact = database.get_monthly_news_flow(ticker)
    if not artefact:
        return {
            "ticker": utils.to_display_ticker(ticker),
            "content": None,
            "generated_at": None,
            "model_name": None,
        }
    return {
        "ticker": utils.to_display_ticker(ticker),
        "content": artefact["content"],
        "generated_at": artefact.get("generated_at"),
        "model_name": artefact.get("model_name"),
    }


@app.get("/ticker/{ticker}/events")
def ticker_events(ticker: KnownTicker):
    """Return the event timeline for a ticker."""
    events = database.get_ticker_events(ticker)
    return {"ticker": utils.to_display_ticker(ticker), "events": events or []}


# ── X feed ─────────────────────────────────────────────────────────────────


@app.get("/x/days")
def x_days():
    """UTC days with stored X posts, newest first."""
    return {"days": x_store.days_with_posts()}


@app.get("/x/posts")
def x_posts(day: Annotated[date, Query(alias="date")]):
    """
    One UTC day of stored X posts, grouped by account group, each with its Jev scores
    and rank (x_jev.rank_score), plus that day's fetch, count and scoring runs.
    """
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    end = start + timedelta(days=1)
    groups: dict[str, list[dict]] = {"ai": [], "macro": [], "wire": []}
    for p in x_store.posts_between(start, end, x_jev.QSET):
        p["rank"] = x_jev.rank_score(p["scores"], p["group_name"])
        groups.setdefault(p["group_name"], []).append(p)
    return {
        "date": day.isoformat(),
        "qset": x_jev.QSET,
        "judging": x_store.is_local_db(),
        "groups": groups,
        "runs": x_store.runs_for_window(start, end),
    }


class Judgment(BaseModel):
    post_id: str
    verdict: Literal["keep", "skip"] | None


@app.post("/x/judgments")
def x_judge(j: Judgment):
    """Your keep/skip verdict on a post (null clears it). Local database only: it's a dev tool."""
    if not x_store.is_local_db():
        raise HTTPException(status_code=403, detail="Judging is only enabled on a local database")
    x_sources.append_judgment(int(j.post_id), j.verdict)
    x_store.set_judgment(int(j.post_id), j.verdict)
    return {"post_id": j.post_id, "verdict": j.verdict}


# ── Front page ─────────────────────────────────────────────────────────────

# Post fields the front page shows; scores and verdicts stay on the dev feed.
_FRONT_POST_FIELDS = (
    "post_id",
    "author_handle",
    "author_name",
    "created_at",
    "text",
    "url",
    "likes",
    "reposts",
    "views",
    "media",
    "links",
    "quoted",
    "group_name",
)


@app.get("/front-page")
def front_page(until: Annotated[datetime | None, Query()] = None):
    """
    The X briefings as of a moment (default: now): per section, the newest 24-hour and 7-day
    briefing written by then, the "top posts" from each briefing's own window, and every post
    they cite. `editions` lists when daily briefings were written, so the page can build its
    date picker in the reader's time zone and ask for the end of a local day.
    """
    editions = x_store.edition_times()
    if not editions:
        return {"editions": [], "digests": {}, "showcase": {}, "posts": {}}
    if until is not None and until.tzinfo is None:
        until = until.replace(tzinfo=UTC)

    digests = {
        s: {p: x_store.latest_digest(s, p, until) for p in ("day", "week")}
        for s in x_digest.SECTIONS
    }
    posts, showcase = {}, {}
    for period in ("day", "week"):
        ref = digests["ai"][period] or digests["macro"][period]
        window = (
            x_store.posts_between(ref["window_start"], ref["window_end"], x_jev.QSET) if ref else []
        )
        posts.update({p["post_id"]: p for p in window})
        showcase[period] = x_digest.showcase(window, {"ai": 4, "macro": 2})

    cited = {
        pid for d in digests.values() for g in d.values() if g for pid in g["content"]["post_ids"]
    }
    missing = [int(pid) for pid in cited if pid not in posts]
    posts.update({p["post_id"]: p for p in x_store.posts_by_ids(missing, x_jev.QSET)})
    wanted = cited | set(showcase["day"]) | set(showcase["week"])
    return {
        "editions": [t.isoformat() for t in editions],
        "digests": digests,
        "showcase": showcase,
        "posts": {
            pid: {k: posts[pid][k] for k in _FRONT_POST_FIELDS} for pid in wanted if pid in posts
        },
    }
