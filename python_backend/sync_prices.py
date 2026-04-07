"""
sync_prices.py — Phase 3A (daily price sync)

Fetches missing daily OHLCV bars from Alpaca for each target ticker
and appends them to market_data.db.

Designed to be run manually or via cron after market close (e.g. 5 PM EST).
It always resumes from the last stored date, so it's safe to run multiple
times or after a gap.

Usage:
    python sync_prices.py
    python sync_prices.py --tickers NVDA AAPL MSFT
"""

import argparse
import time
from datetime import date, timedelta

from alpaca_trade_api.rest import REST, TimeFrame

import market_data
import utils

ALPACA_KEY    = utils.get_env_variable("ALPACA_KEY")
ALPACA_SECRET = utils.get_env_variable("ALPACA_SECRET")

DEFAULT_TICKERS = ["NVDA", "AAPL", "MSFT"]
SLEEP_BETWEEN   = 0.5   # seconds between tickers


def sync_ticker(api: REST, ticker: str):
    latest = market_data.get_latest_price_date(ticker)
    end    = str(date.today() - timedelta(days=1))  # yesterday (today's bar not closed)

    if latest is None:
        print(f"  [{ticker}] No data found — run backfill_prices.py first for a full history.")
        return

    if latest >= end:
        print(f"  [{ticker}] Already up to date ({latest}).")
        return

    # Resume from the day after our last row
    from datetime import datetime
    start = str((datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)).date())
    print(f"  [{ticker}] Syncing {start} → {end} ...")

    bars = api.get_bars(
        ticker,
        TimeFrame.Day,
        start=start,
        end=end,
        adjustment="split",
        feed="iex",
        limit=1000,
    ).df

    if bars.empty:
        print(f"  [{ticker}] No new bars (market may have been closed).")
        return

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
            "vwap":        float(row["vwap"])      if "vwap"        in row else None,
            "trade_count": int(row["trade_count"]) if "trade_count" in row else None,
        })

    inserted = market_data.bulk_insert_prices(rows)
    print(f"  [{ticker}] Inserted {inserted} row(s) — now up to {rows[-1]['date']}")


def main():
    parser = argparse.ArgumentParser(description="Daily price sync from Alpaca")
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    args = parser.parse_args()

    market_data.init_db()

    api = REST(
        key_id=ALPACA_KEY,
        secret_key=ALPACA_SECRET,
        base_url="https://paper-api.alpaca.markets/v2",
    )

    print(f"\nSyncing {len(args.tickers)} ticker(s): {args.tickers}\n")
    for ticker in args.tickers:
        try:
            sync_ticker(api, ticker)
        except Exception as e:
            print(f"  [{ticker}] ERROR: {e}")
        time.sleep(SLEEP_BETWEEN)

    print("\nDone.")
    market_data.status()


if __name__ == "__main__":
    main()
