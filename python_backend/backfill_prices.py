"""
backfill_prices.py — Task 2B

Fetches 5 years of daily OHLCV bars from Alpaca for a target ticker list
and bulk-inserts them into market_data.db → historical_prices.

Usage:
    python backfill_prices.py
    python backfill_prices.py --tickers NVDA AAPL MSFT TSLA
    python backfill_prices.py --tickers NVDA --start 2020-01-01

Requires env vars: ALPACA_KEY, ALPACA_SECRET
"""

import argparse
import time
from datetime import date, timedelta

import market_data
import utils

ALPACA_KEY    = utils.get_env_variable("ALPACA_KEY")
ALPACA_SECRET = utils.get_env_variable("ALPACA_SECRET")

DEFAULT_TICKERS  = ["NVDA", "AAPL", "MSFT"]
LOOKBACK_YEARS   = 5
SLEEP_BETWEEN    = 1.0   # seconds between tickers (rate-limit courtesy)


def fetch_bars(ticker: str, start: str, end: str) -> list[dict]:
    """
    Fetches daily OHLCV bars from Alpaca REST API.
    Returns a list of dicts ready for bulk_insert_prices().
    """
    from alpaca_trade_api.rest import REST, TimeFrame

    api = REST(
        key_id=ALPACA_KEY,
        secret_key=ALPACA_SECRET,
        base_url="https://paper-api.alpaca.markets/v2",
    )

    print(f"  Fetching {ticker} bars {start} → {end} ...")
    bars = api.get_bars(
        ticker,
        TimeFrame.Day,
        start=start,
        end=end,
        adjustment="split",   # split-adjusted closes
        feed="iex",           # free tier (IEX feed)
        limit=10000,
    ).df

    if bars.empty:
        print(f"  [{ticker}] No bars returned.")
        return []

    rows = []
    for ts, row in bars.iterrows():
        rows.append({
            "ticker":      ticker,
            "date":        str(ts.date()),
            "open":        float(row["open"]),
            "high":        float(row["high"]),
            "low":         float(row["low"]),
            "close":       float(row["close"]),
            "volume":      int(row["volume"]),
            "vwap":        float(row["vwap"])        if "vwap"        in row else None,
            "trade_count": int(row["trade_count"])   if "trade_count" in row else None,
        })

    return rows


def backfill_ticker(ticker: str, start: str, end: str):
    # Check what we already have — skip days we already stored
    latest = market_data.get_latest_price_date(ticker)
    if latest and latest >= end:
        print(f"  [{ticker}] Already up to date ({latest}). Skipping.")
        return
    if latest and latest > start:
        # Resume from the day after our last row
        from datetime import datetime
        resume = str((datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)).date())
        print(f"  [{ticker}] Resuming from {resume} (had data up to {latest})")
        start = resume

    rows = fetch_bars(ticker, start, end)
    if rows:
        inserted = market_data.bulk_insert_prices(rows)
        print(f"  [{ticker}] Inserted {inserted} rows ({rows[0]['date']} → {rows[-1]['date']})")
    else:
        print(f"  [{ticker}] Nothing to insert.")


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
