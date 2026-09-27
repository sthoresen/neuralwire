"""
sync_prices.py — daily price sync

Fetches missing daily OHLCV bars from Alpaca for each target ticker.
Resumes from the last stored date — safe to run multiple times or after a gap.

Usage:
    python sync_prices.py
    python sync_prices.py --tickers NVDA AAPL MSFT
"""

import argparse
import time
from datetime import date, datetime, timedelta

import alpaca
import market_data
import utils

DEFAULT_TICKERS = ["NVDA", "AAPL", "MSFT"]
SLEEP_BETWEEN = 0.5


def sync_ticker(ticker: str):
    latest = market_data.get_latest_price_date(ticker)
    end = str(date.today())

    if latest is None:
        print(f"  [{ticker}] No data — run backfill_prices.py first.")
        return

    if latest >= end:
        print(f"  [{ticker}] Already up to date ({latest}).")
        return

    start = str((datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)).date())
    print(f"  [{ticker}] Syncing {start} → {end} ...")

    bars = alpaca.get_bars(utils.to_alpaca_ticker(ticker), "1Day", start, end)
    if not bars:
        print(f"  [{ticker}] No new bars.")
        return

    rows = alpaca.bars_to_daily_rows(ticker, bars)
    inserted = market_data.bulk_insert_prices(rows)
    print(f"  [{ticker}] Inserted {inserted} row(s) → {rows[-1]['date']}")


def main():
    parser = argparse.ArgumentParser(description="Daily price sync from Alpaca")
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    args = parser.parse_args()

    market_data.init_db()

    print(f"\nSyncing {len(args.tickers)} ticker(s): {args.tickers}\n")
    for ticker in args.tickers:
        try:
            sync_ticker(ticker)
        except Exception as e:
            print(f"  [{ticker}] ERROR: {e}")
        time.sleep(SLEEP_BETWEEN)

    print("\nDone.")
    market_data.status()


if __name__ == "__main__":
    main()
