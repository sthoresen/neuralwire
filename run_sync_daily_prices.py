"""Runner: sync yesterday's daily OHLCV bars from Alpaca for all active tickers."""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python_backend"))

import database
import market_data
import sync_prices

SLEEP_BETWEEN = 0.5


if __name__ == "__main__":
    tickers = database.get_active_tickers()
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
