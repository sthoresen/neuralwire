"""Worker behaviour when a dependency fails: database, Alpha Vantage, defeatbeta."""

import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import psycopg2
import pytest
import requests

import analysis
import ingest_earnings
import news_reporter
import run_analysis

KEY = "SECRET-TEST-KEY"
REPO_ROOT = Path(__file__).resolve().parent.parent

# ── Analysis loop ────────────────────────────────────────────────────────────


def test_loop_process_does_not_load_the_analysis_code():
    # The always-on loop must stay small: the analysis code (and the memory
    # it uses) belongs only in the short-lived batch processes.
    check = "import run_analysis, sys; print('analysis' in sys.modules, 'psycopg2' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", check], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )

    assert result.stdout.split() == ["False", "False"]


def test_each_batch_runs_in_a_fresh_process(monkeypatch):
    started = []

    def fake_run(args):
        started.append(args)
        return subprocess.CompletedProcess(args, run_analysis.IDLE)

    def stop_after_first_wait(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(run_analysis.subprocess, "run", fake_run)
    monkeypatch.setattr(run_analysis.time, "sleep", stop_after_first_wait)

    with pytest.raises(KeyboardInterrupt):
        run_analysis.run_forever()

    assert started == [[sys.executable, str(REPO_ROOT / "run_analysis.py"), "--once"]]


def test_database_outage_ends_the_batch_with_a_retry_code(monkeypatch):
    def unreachable(limit):
        raise psycopg2.OperationalError("server closed the connection unexpectedly")

    monkeypatch.setattr(analysis, "run_pending_pipeline", unreachable)

    assert run_analysis.run_batch() == run_analysis.DB_UNAVAILABLE


@pytest.mark.parametrize(
    "batch_size, advanced, expected_code",
    [
        (20, 20, run_analysis.CONTINUE),  # backlog cleared some articles: go again
        (20, 0, run_analysis.IDLE),  # stuck on the same articles: back off
        (0, 0, run_analysis.IDLE),  # nothing pending
    ],
)
def test_batch_exit_code(monkeypatch, batch_size, advanced, expected_code):
    stats = SimpleNamespace(batch_size=batch_size, advanced=advanced, llm_error=0, no_content=0)
    monkeypatch.setattr(analysis, "run_pending_pipeline", lambda limit: stats)

    assert run_analysis.run_batch() == expected_code


@pytest.mark.parametrize(
    "exit_code, expected_wait",
    [
        (run_analysis.CONTINUE, 0),
        (run_analysis.IDLE, run_analysis.COOLDOWN),
        (run_analysis.DB_UNAVAILABLE, run_analysis.RETRY),
        (1, run_analysis.RETRY),  # uncaught exception in the batch
        (-9, run_analysis.RETRY),  # batch killed, e.g. out of memory
    ],
)
def test_wait_after_batch_exits(exit_code, expected_wait):
    assert run_analysis.wait_after(exit_code) == expected_wait


# ── Alpha Vantage ────────────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


@pytest.fixture
def saved(monkeypatch):
    rows = []
    monkeypatch.setattr(news_reporter.database, "insert_raw_article", lambda t, a: rows.append(a))
    return rows


def test_feed_articles_are_saved(monkeypatch, saved):
    feed = [{"url": "https://a", "title": "A", "summary": "s", "time_published": "20260930T120000"}]
    monkeypatch.setattr(news_reporter.requests, "get", lambda *a, **k: FakeResponse({"feed": feed}))

    news_reporter.get_alpha_vantage_news(KEY, "NVDA")

    assert [row["url"] for row in saved] == ["https://a"]


def test_missing_feed_logs_the_message_without_the_key(monkeypatch, saved, capsys):
    throttled = {"Information": f"We have detected your API key as {KEY} and our rate limit is..."}
    monkeypatch.setattr(news_reporter.requests, "get", lambda *a, **k: FakeResponse(throttled))

    news_reporter.get_alpha_vantage_news(KEY, "NVDA")

    output = capsys.readouterr().out
    assert "No feed in response: We have detected your API key as <key>" in output
    assert KEY not in output
    assert saved == []


def test_http_errors_are_logged_without_the_key(monkeypatch, capsys):
    def fail(*_args, **_kwargs):
        raise requests.HTTPError(f"503 Server Error for url: https://x/query?apikey={KEY}")

    monkeypatch.setattr(news_reporter.requests, "get", fail)

    news_reporter.get_alpha_vantage_news(KEY, "NVDA")

    output = capsys.readouterr().out
    assert "apikey=<key>" in output
    assert KEY not in output


# ── Earnings transcripts ─────────────────────────────────────────────────────


def test_transcript_source_failure_raises_an_exception_not_systemexit(monkeypatch):
    # run_earnings.py catches Exception per ticker; SystemExit would end the whole job.
    def broken_ticker(symbol):
        raise ConnectionError("404 Not Found")

    fake = types.ModuleType("defeatbeta_api.data.ticker")
    fake.Ticker = broken_ticker
    # Stub the whole package path so the real defeatbeta_api (and its network
    # calls on import) is never loaded.
    for name in ("defeatbeta_api", "defeatbeta_api.data"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "defeatbeta_api.data.ticker", fake)

    with pytest.raises(RuntimeError, match="NVDA"):
        ingest_earnings.run(
            ticker="NVDA", limit=4, fiscal_year=None, fiscal_quarter=None, commit=False
        )
