"""Runner: refresh stale ticker artefacts (monthly_news_flow, focal_points). Intended for Railway cron."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python_backend"))

import analysis
import database

MAX_AGE_DAYS = 7


if __name__ == "__main__":
    tickers = database.get_active_tickers()
    print(f"=== run_artefacts: {len(tickers)} ticker(s), max_age={MAX_AGE_DAYS}d ===")

    for ticker in tickers:
        try:
            analysis.refresh_ticker_artefacts(ticker, max_age_days=MAX_AGE_DAYS)
        except Exception as e:
            print(f"[{ticker}] ERROR: {e}")

    print("=== Done ===")
