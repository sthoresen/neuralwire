"""
API tests. No database: the database / market-data functions each endpoint
calls are replaced per test, and TestClient is used without `with`, so the
lifespan startup (table creation) doesn't run unless a test asks for it.
"""

import pytest
from fastapi.testclient import TestClient

import api

client = TestClient(api.app)

WATCHLIST = ["NVDA", "BRKB"]


@pytest.fixture(autouse=True)
def watchlist(monkeypatch):
    """Every /ticker/{ticker}/... request checks the watchlist first; stub it for all tests."""
    monkeypatch.setattr(api.database, "get_active_tickers", lambda: WATCHLIST)


class FakeConn:
    """Minimal psycopg2 connection: returns `rows` and records the query params."""

    def __init__(self, rows):
        self.rows = rows
        self.params = None

    def cursor(self):
        return self

    def execute(self, _sql, params):
        self.params = params

    def fetchall(self):
        return self.rows

    def close(self):
        pass


def _article(url, headline="Headline"):
    return {
        "impact_headline": "Impact",
        "headline": headline,
        "url": url,
        "published_at": "2026-09-01T12:00:00",
        "provider": "Alpha Vantage",
        "ai_summary": "Summary",
        "importance_score": 70,
        "relevancy_score": 80,
        "breaking_news_score": 10,
        "ticker": "NVDA",
    }


# ── Startup ──────────────────────────────────────────────────────────────────


def test_startup_initialises_database(monkeypatch):
    ran = []
    for module, fn in [
        (api.database, "init_db"),
        (api.database, "init_ticker_artefacts_table"),
        (api.database, "migrate_add_earnings_tables"),
        (api.tc, "init"),
        (api.tclass, "init"),
    ]:
        monkeypatch.setattr(module, fn, lambda fn=fn: ran.append(fn))

    with TestClient(api.app):  # entering the context runs the lifespan
        pass

    assert ran == [
        "init_db",
        "init_ticker_artefacts_table",
        "migrate_add_earnings_tables",
        "init",
        "init",
    ]


def test_health_returns_ok_without_touching_the_db():
    # Railway waits for this before switching traffic to a new deploy, so if it
    # breaks, every web deploy fails. It must also never need the database.
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ── Ticker mapping ───────────────────────────────────────────────────────────


def test_tickers_are_returned_in_display_format(monkeypatch):
    monkeypatch.setattr(api.database, "get_active_tickers", lambda: ["BRKB", "NVDA"])

    response = client.get("/tickers")

    assert response.status_code == 200
    assert response.json() == {"tickers": ["BRK.B", "NVDA"]}


def test_url_ticker_is_normalised_before_querying(monkeypatch):
    queried = []
    monkeypatch.setattr(api.database, "get_ticker_events", lambda t: queried.append(t) or [])

    response = client.get("/ticker/brk.b/events")

    assert queried == ["BRKB"]  # lowercase display form in, DB form out
    assert response.json() == {"ticker": "BRK.B", "events": []}


TICKER_ENDPOINTS = [
    "header",
    "prices",
    "intraday",
    "articles",
    "coverage",
    "focal-points",
    "monthly-news-flow",
    "events",
]


@pytest.mark.parametrize("endpoint", TICKER_ENDPOINTS)
def test_unknown_ticker_is_404_and_triggers_no_work(monkeypatch, endpoint):
    def must_not_run(*_args, **_kwargs):
        raise AssertionError("no lookups, LLM calls or queries for unknown tickers")

    for module, fn in [
        (api.tclass, "get_or_fetch"),
        (api.tc, "resolve"),
        (api.market_data, "get_prices"),
        (api.market_data, "get_intraday"),
        (api.database, "get_ticker_artefact"),
        (api.database, "get_focal_points"),
        (api.database, "get_monthly_news_flow"),
        (api.database, "get_ticker_events"),
        (api, "get_conn"),
    ]:
        monkeypatch.setattr(module, fn, must_not_run)

    response = client.get(f"/ticker/HEALTH/{endpoint}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown ticker: HEALTH"}


# ── Artefacts ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["focal-points", "monthly-news-flow"])
def test_missing_artefact_returns_nulls_not_error(monkeypatch, path):
    monkeypatch.setattr(api.database, "get_focal_points", lambda t: None)
    monkeypatch.setattr(api.database, "get_monthly_news_flow", lambda t: None)

    response = client.get(f"/ticker/NVDA/{path}")

    assert response.status_code == 200
    assert response.json() == {
        "ticker": "NVDA",
        "content": None,
        "generated_at": None,
        "model_name": None,
    }


def test_existing_artefact_is_returned(monkeypatch):
    artefact = {"content": "Brief", "generated_at": "2026-09-01", "model_name": "std-a"}
    monkeypatch.setattr(api.database, "get_focal_points", lambda t: artefact)

    response = client.get("/ticker/NVDA/focal-points")

    assert response.json() == {"ticker": "NVDA", **artefact}


def test_header_falls_back_to_generic_description(monkeypatch):
    monkeypatch.setattr(
        api.tclass, "get_or_fetch", lambda t: {"long_name": "NVIDIA Corp", "exchange": "NASDAQ"}
    )
    monkeypatch.setattr(api.tc, "resolve", lambda t: "#76b900")
    monkeypatch.setattr(api.database, "get_ticker_artefact", lambda t, kind: None)

    body = client.get("/ticker/NVDA/header").json()

    assert body["description"] == "NVIDIA Corp is a publicly traded company."
    assert body["accent"] == "#76b900"
    assert body["accent_dim"] == "rgba(118,185,0,0.1)"


# ── Articles & coverage ──────────────────────────────────────────────────────


def test_articles_passes_ticker_and_filters_to_the_query(monkeypatch):
    conn = FakeConn([_article("https://a")])
    monkeypatch.setattr(api, "get_conn", lambda dict_cursor=False: conn)

    response = client.get("/ticker/NVDA/articles?lookback_days=7&limit=5")

    assert response.status_code == 200
    assert conn.params == ("NVDA", 7, 5)
    assert response.json()["articles"][0]["url"] == "https://a"


def test_coverage_deduplicates_articles_by_url(monkeypatch):
    # The same article analysed twice (e.g. re-run) must show up once.
    rows = [_article("https://a", "first"), _article("https://a", "dupe"), _article("https://b")]
    monkeypatch.setattr(api, "get_conn", lambda dict_cursor=False: FakeConn(rows))

    articles = client.get("/ticker/NVDA/coverage").json()["articles"]

    assert [a["url"] for a in articles] == ["https://a", "https://b"]
    assert articles[0]["headline"] == "first"  # keeps the first (newest) one


@pytest.mark.parametrize(
    "query",
    [
        "/ticker/NVDA/articles?limit=999",
        "/ticker/NVDA/articles?lookback_days=0",
        "/ticker/NVDA/articles?limit=abc",
        "/ticker/NVDA/coverage?min_relevance=101",
    ],
)
def test_invalid_query_params_are_rejected_before_touching_the_db(monkeypatch, query):
    def fail(*_args, **_kwargs):
        raise AssertionError("database should not be queried")

    monkeypatch.setattr(api, "get_conn", fail)

    assert client.get(query).status_code == 422
