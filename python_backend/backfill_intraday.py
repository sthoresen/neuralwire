"""
backfill_intraday.py — fetch up to 30 days of 1-minute OHLCV bars from Alpaca.

Usage:
    python backfill_intraday.py
    python backfill_intraday.py --tickers NVDA AAPL --days 30
"""

import argparse
import time
from datetime import date, datetime, timedelta

import alpaca
import market_data
import utils

DEFAULT_TICKERS = ["NVDA", "AAPL", "MSFT"]
MAX_DAYS        = 30
SLEEP_BETWEEN   = 0.5


def backfill_ticker(ticker: str, days: int):
    end   = str(date.today())
    start = str(date.today() - timedelta(days=days))

    latest = market_data.get_latest_intraday_timestamp(ticker)
    if latest:
        latest_date = latest[:10]
        if latest_date >= str(date.today() - timedelta(days=1)):
            print(f"  [{ticker}] Already up to date ({latest_date}). Skipping.")
            return
        resume = str((datetime.strptime(latest_date, "%Y-%m-%d") + timedelta(days=1)).date())
        if resume > start:
            start = resume
            print(f"  [{ticker}] Resuming from {start}")

    print(f"  [{ticker}] Fetching 1-min bars {start} → {end} ...")
    bars = alpaca.get_bars(utils.to_alpaca_ticker(ticker), "1Min", start, end)

    if not bars:
        print(f"  [{ticker}] No bars returned.")
        return

    rows = alpaca.bars_to_intraday_rows(ticker, bars)
    inserted = market_data.bulk_insert_intraday(rows)
    print(f"  [{ticker}] Inserted {inserted} minute bars ({rows[0]['timestamp'][:10]} → {rows[-1]['timestamp'][:10]})")


def main():
    parser = argparse.ArgumentParser(description="Backfill intraday minute bars from Alpaca")
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    parser.add_argument("--days", type=int, default=MAX_DAYS)
    args = parser.parse_args()

    days = min(args.days, MAX_DAYS)
    market_data.init_db()

    print(f"\nBackfilling intraday for {len(args.tickers)} ticker(s): {args.tickers} ({days} days)\n")
    for ticker in args.tickers:
        try:
            backfill_ticker(ticker, days)
        except Exception as e:
            print(f"  [{ticker}] ERROR: {e}")
        time.sleep(SLEEP_BETWEEN)

    print("\nDone.")
    market_data.status()


if __name__ == "__main__":
    main()
