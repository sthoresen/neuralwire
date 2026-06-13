"""Runner: sync intraday (1-min) bars from Alpaca for all active tickers. Intended for Railway cron."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python_backend"))

import database
import market_data
import sync_intraday

SLEEP_BETWEEN = 0.5


if __name__ == "__main__":
    tickers = database.get_active_tickers()
    print(f"=== run_sync_intraday: {len(tickers)} ticker(s) ===")

    market_data.init_db()

    for ticker in tickers:
        try:
            sync_intraday.sync_ticker_intraday(ticker)
        except Exception as e:
            print(f"  [{ticker}] ERROR: {e}")
        time.sleep(SLEEP_BETWEEN)

    sync_intraday.prune_old_intraday(days_keep=7)
    print("=== Done ===")
