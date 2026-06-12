"""Runner: ingest latest earnings transcripts for all active tickers. Intended for Railway cron."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python_backend"))

from db_connection import get_conn
import ingest_earnings
import analysis

LIMIT = 4  # most recent quarters per ticker


def get_active_tickers() -> list[str]:
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
    except Exception as e:
        print(f"WARNING: could not query tickers ({e}), falling back to NVDA")
        return ["NVDA"]


if __name__ == "__main__":
    tickers = get_active_tickers()
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
