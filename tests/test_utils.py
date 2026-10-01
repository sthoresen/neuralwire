from datetime import datetime

import pytest

import utils

# ── Ticker normalisation ─────────────────────────────────────────────────────


def test_display_ticker_restores_dot():
    assert utils.to_display_ticker("BRKB") == "BRK.B"


def test_db_ticker_removes_dot():
    assert utils.to_db_ticker("BRK.B") == "BRKB"


@pytest.mark.parametrize("ticker", ["BRKB", "BRKA", "NVDA", "AAPL"])
def test_display_and_db_ticker_round_trip(ticker):
    assert utils.to_db_ticker(utils.to_display_ticker(ticker)) == ticker


def test_unmapped_ticker_passes_through_unchanged():
    assert utils.to_display_ticker("NVDA") == "NVDA"
    assert utils.to_yfinance_ticker("NVDA") == "NVDA"


def test_yfinance_ticker_uses_dash():
    assert utils.to_yfinance_ticker("BRKB") == "BRK-B"


# ── parse_datetime ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("20260409T143000", datetime(2026, 4, 9, 14, 30)),  # Alpha Vantage
        ("2026-04-09T14:30:00Z", datetime(2026, 4, 9, 14, 30)),  # ISO 8601
        ("2026-04-09 14:30:00", datetime(2026, 4, 9, 14, 30)),
        ("2026-04-09", datetime(2026, 4, 9)),
    ],
)
def test_parse_datetime_known_formats(raw, expected):
    assert utils.parse_datetime(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "not a date"])
def test_parse_datetime_returns_none_for_missing_or_unparseable(raw):
    assert utils.parse_datetime(raw) is None


# ── clean_json_response ──────────────────────────────────────────────────────


def test_clean_json_plain_list():
    assert utils.clean_json_response('[{"a": 1}]') == [{"a": 1}]


def test_clean_json_strips_markdown_fences():
    raw = '```json\n[{"a": 1}]\n```'
    assert utils.clean_json_response(raw) == [{"a": 1}]


def test_clean_json_ignores_prose_around_the_list():
    raw = 'Sure! Here are the events:\n[{"a": 1}]\nLet me know if you need more.'
    assert utils.clean_json_response(raw) == [{"a": 1}]


def test_clean_json_wraps_single_object_in_list():
    assert utils.clean_json_response('{"a": 1}') == [{"a": 1}]


@pytest.mark.parametrize("raw", [None, "", "[not valid json]"])
def test_clean_json_returns_empty_list_on_missing_or_invalid(raw):
    assert utils.clean_json_response(raw) == []


# ── csv_to_tickers ───────────────────────────────────────────────────────────


def test_csv_to_tickers_basic():
    assert utils.csv_to_tickers("AAPL,NVDA") == ["AAPL", "NVDA"]


def test_csv_to_tickers_trailing_comma():
    assert utils.csv_to_tickers("AAPL,NVDA,") == ["AAPL", "NVDA"]


def test_csv_to_tickers_boring_means_no_tickers():
    assert utils.csv_to_tickers("boring!") == []


def test_csv_to_tickers_tolerates_spaces_after_commas():
    # LLMs often write "AAPL, NVDA" even when the prompt example has no spaces.
    # " NVDA" would then silently fail the watchlist filter in analysis.py.
    assert utils.csv_to_tickers("AAPL, NVDA") == ["AAPL", "NVDA"]


@pytest.mark.parametrize("raw", ["AAPL,NVDA, ", "AAPL, ,NVDA", " AAPL,NVDA\n", "AAPL,,NVDA,"])
def test_csv_to_tickers_never_returns_empty_tickers(raw):
    assert utils.csv_to_tickers(raw) == ["AAPL", "NVDA"]


@pytest.mark.parametrize("raw", [None, "", "   ", 42])
def test_csv_to_tickers_signals_failed_llm_output_with_minus_one(raw):
    assert utils.csv_to_tickers(raw) == -1


# ── url_domain ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.reuters.com/markets/us/some-story", "reuters.com"),
        ("https://finance.yahoo.com/news/x.html", "finance.yahoo.com"),  # subdomains are kept
        ("http://marketbeat.com/a?b=c", "marketbeat.com"),
    ],
)
def test_url_domain_extracts_the_publisher(url, expected):
    assert utils.url_domain(url) == expected


@pytest.mark.parametrize("url", [None, "", "not a url"])
def test_url_domain_is_unknown_for_missing_or_bad_urls(url):
    assert utils.url_domain(url) == "unknown"
