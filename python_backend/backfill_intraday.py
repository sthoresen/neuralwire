"""
backfill_intraday.py

Fetches up to 30 days of 1-minute OHLCV bars from Alpaca (free tier limit)
and stores them in market_data.db → intraday_prices.

Used to power the 1D and 5D chart views.

Usage:
    python backfill_intraday.py
    python backfill_intraday.py --tickers NVDA AAPL --days 30

Requires env vars: ALPACA_KEY, ALPACA_SECRET
"""

import argparse
import time
from datetime import date, timedelta, datetime

from alpaca_trade_api.rest import REST, TimeFrame

import market_data
import utils

ALPACA_KEY    = utils.get_env_variable("ALPACA_KEY")
ALPACA_SECRET = utils.get_env_variable("ALPACA_SECRET")

DEFAULT_TICKERS = ["NVDA", "AAPL", "MSFT"]
MAX_DAYS        = 30    # Alpaca free tier limit for minute bars
SLEEP_BETWEEN   = 0.5


def backfill_ticker(api: REST, ticker: str, days: int):
    start = str(date.today() - timedelta(days=days))
    end   = str(date.today())

    # Resume from last stored timestamp if we have recent data
    latest = market_data.get_latest_intraday_timestamp(ticker)
    if latest:
        latest_date = latest[:10]
        if latest_date >= str(date.today() - timedelta(days=1)):
            print(f"  [{ticker}] Intraday already up to date ({latest_date}). Skipping.")
            return
        resume = str((datetime.strptime(latest_date, "%Y-%m-%d") + timedelta(days=1)).date())
        if resume > start:
            start = resume
            print(f"  [{ticker}] Resuming intraday from {start}")

    print(f"  [{ticker}] Fetching 1-min bars {start} → {end} ...")

    bars = api.get_bars(
        ticker,
        TimeFrame.Minute,
        start=start,
        end=end,
        adjustment="split",
        feed="iex",
        limit=100_000,
    ).df

    if bars.empty:
        print(f"  [{ticker}] No intraday bars returned.")
        return

    rows = []
    for ts, row in bars.iterrows():
        rows.append({
            "ticker":      ticker,
            "timestamp":   ts.isoformat(),
            "open":        float(row["open"]),
            "high":        float(row["high"]),
            "low":         float(row["low"]),
            "close":       float(row["close"]),
            "volume":      int(row["volume"]),
            "vwap":        float(row["vwap"])      if "vwap"        in row else None,
            "trade_count": int(row["trade_count"]) if "trade_count" in row else None,
        })

    inserted = market_data.bulk_insert_intraday(rows)
    print(f"  [{ticker}] Inserted {inserted} minute bars ({rows[0]['timestamp'][:10]} → {rows[-1]['timestamp'][:10]})")


def main():
    parser = argparse.ArgumentParser(description="Backfill intraday minute bars from Alpaca")
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    parser.add_argument("--days", type=int, default=MAX_DAYS,
                        help=f"Days of history to fetch (max {MAX_DAYS} on free tier)")
    args = parser.parse_args()

    days = min(args.days, MAX_DAYS)
    market_data.init_db()

    api = REST(
        key_id=ALPACA_KEY,
        secret_key=ALPACA_SECRET,
        base_url="https://paper-api.alpaca.markets/v2",
    )

    print(f"\nBackfilling intraday for {len(args.tickers)} ticker(s): {args.tickers} ({days} days)\n")
    for ticker in args.tickers:
        try:
            backfill_ticker(api, ticker, days)
        except Exception as e:
            print(f"  [{ticker}] ERROR: {e}")
        time.sleep(SLEEP_BETWEEN)

    print("\nDone.")
    market_data.status()


if __name__ == "__main__":
    main()
