"""
Runner: backfill 5 years of daily OHLCV bars from Alpaca for all active tickers.

One-time / on-demand — run when a new ticker is added.
Safe to re-run: resumes from last stored date, skips tickers already up to date.

Usage:
    python run_backfill_historical_prices.py
    python run_backfill_historical_prices.py --tickers AAPL TSLA
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python_backend"))

import backfill_prices
import database
import market_data

SLEEP_BETWEEN = 1.0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill historical prices for all active tickers")
    parser.add_argument("--tickers", nargs="+", default=None)
    args = parser.parse_args()

    tickers = args.tickers if args.tickers else database.get_active_tickers()
    print(f"=== run_backfill_historical_prices: {len(tickers)} ticker(s): {tickers} ===")

    market_data.init_db()

    from datetime import date, timedelta
    start = str(date.today() - timedelta(days=365 * 5))
    end   = str(date.today())

    for ticker in tickers:
        try:
            backfill_prices.backfill_ticker(ticker, start, end)
        except Exception as e:
            print(f"  [{ticker}] ERROR: {e}")
        time.sleep(SLEEP_BETWEEN)

    print("=== Done ===")
    market_data.status()
