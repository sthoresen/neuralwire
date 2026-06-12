"""Runner: refresh stale ticker artefacts (monthly_news_flow, focal_points). Intended for Railway cron."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python_backend"))

from db_connection import get_conn
import analysis

MAX_AGE_DAYS = 7


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
    print(f"=== run_artefacts: {len(tickers)} ticker(s), max_age={MAX_AGE_DAYS}d ===")

    for ticker in tickers:
        try:
            analysis.refresh_ticker_artefacts(ticker, max_age_days=MAX_AGE_DAYS)
        except Exception as e:
            print(f"[{ticker}] ERROR: {e}")

    print("=== Done ===")
