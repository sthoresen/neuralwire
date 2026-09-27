"""
backfill_prices.py — fetch 5 years of daily OHLCV bars from Alpaca.

Usage:
    python backfill_prices.py
    python backfill_prices.py --tickers NVDA AAPL MSFT TSLA
    python backfill_prices.py --tickers NVDA --start 2020-01-01
"""

import argparse
import time
from datetime import date, datetime, timedelta

import alpaca
import market_data
import utils

DEFAULT_TICKERS = ["NVDA", "AAPL", "MSFT"]
LOOKBACK_YEARS  = 5
SLEEP_BETWEEN   = 1.0


def fetch_bars(alpaca_ticker: str, start: str, end: str) -> list[dict]:
    print(f"  Fetching {alpaca_ticker} bars {start} → {end} ...")
    return alpaca.get_bars(alpaca_ticker, "1Day", start, end)


def backfill_ticker(ticker: str, start: str, end: str):
    latest = market_data.get_latest_price_date(ticker)
    if latest and latest >= end:
        print(f"  [{ticker}] Already up to date ({latest}). Skipping.")
        return

    effective_start = start
    if latest and latest > start:
        effective_start = str((datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)).date())
        print(f"  [{ticker}] Resuming from {effective_start}")

    bars = fetch_bars(utils.to_alpaca_ticker(ticker), effective_start, end)
    if not bars:
        print(f"  [{ticker}] Nothing to insert.")
        return

    rows = alpaca.bars_to_daily_rows(ticker, bars)
    inserted = market_data.bulk_insert_prices(rows)
    print(f"  [{ticker}] Inserted {inserted} rows ({rows[0]['date']} → {rows[-1]['date']})")


def main():
    parser = argparse.ArgumentParser(description="Backfill 5-year OHLCV prices from Alpaca")
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    parser.add_argument("--start",   default=str(date.today() - timedelta(days=365 * LOOKBACK_YEARS)))
    parser.add_argument("--end",     default=str(date.today()))
    args = parser.parse_args()

    market_data.init_db()

    print(f"\nBackfilling {len(args.tickers)} ticker(s): {args.tickers}")
    print(f"Date range: {args.start} → {args.end}\n")

    for ticker in args.tickers:
        try:
            backfill_ticker(ticker, args.start, args.end)
        except Exception as e:
            print(f"  [{ticker}] ERROR: {e}")
        time.sleep(SLEEP_BETWEEN)

    print("\nDone.")
    market_data.status()


if __name__ == "__main__":
    main()
