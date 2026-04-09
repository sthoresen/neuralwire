"""Runner: sync yesterday's daily OHLCV bars from Alpaca for all active tickers."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python_backend"))

from db_connection import get_conn
import market_data
import sync_prices

SLEEP_BETWEEN = 0.5


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
    print(f"=== run_sync_daily_prices: {len(tickers)} ticker(s): {tickers} ===")

    market_data.init_db()

    for ticker in tickers:
        try:
            sync_prices.sync_ticker(ticker)
        except Exception as e:
            print(f"  [{ticker}] ERROR: {e}")
        time.sleep(SLEEP_BETWEEN)

    print("=== Done ===")
    market_data.status()
