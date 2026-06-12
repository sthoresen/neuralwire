"""Runner: scan analyzed articles for ticker events. Intended for Railway cron."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python_backend"))

from db_connection import get_conn
import event_pipeline


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
    print(f"=== run_events: {len(tickers)} ticker(s): {tickers} ===")

    for ticker in tickers:
        try:
            approved = event_pipeline.run_event_pipeline(ticker, batch_size=50, commit=True)
            print(f"[{ticker}] {len(approved)} new event(s) added")
        except Exception as e:
            print(f"[{ticker}] ERROR: {e}")

    print("=== Done ===")
