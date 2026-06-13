"""Runner: ingest latest earnings transcripts for all active tickers. Intended for Railway cron."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python_backend"))

import database
import ingest_earnings
import analysis

LIMIT = 4  # most recent quarters per ticker


if __name__ == "__main__":
    tickers = database.get_active_tickers()
    print(f"=== run_earnings: {len(tickers)} ticker(s): {tickers} ===")

    for ticker in tickers:
        try:
            saved = ingest_earnings.run(
                ticker=ticker,
                limit=LIMIT,
                fiscal_year=None,
                fiscal_quarter=None,
                commit=True,
            )
            if saved:
                print(f"[{ticker}] {saved} new transcript(s) — triggering artefact refresh")
                analysis.refresh_ticker_artefacts(ticker, force=True)
        except Exception as e:
            print(f"[{ticker}] ERROR: {e}")

    print("=== Done ===")
