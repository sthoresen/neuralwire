"""
Runner: backfill 5 years of daily OHLCV bars from Alpaca for all active tickers.

This is a one-time / on-demand script — run it when a new ticker is added.
Safe to re-run: resumes from the last stored date, skips tickers already up to date.

Usage:
    python run_backfill_historical_prices.py
    python run_backfill_historical_prices.py --tickers AAPL TSLA   # specific tickers only
"""
import os
import sys
import time
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python_backend"))

from db_connection import get_conn
import market_data
import backfill_prices
import utils

SLEEP_BETWEEN = 1.0


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
    parser = argparse.ArgumentParser(description="Backfill historical prices for all active tickers")
    parser.add_argument("--tickers", nargs="+", default=None, help="Override tickers (default: all active)")
    args = parser.parse_args()

    tickers = args.tickers if args.tickers else get_active_tickers()
    print(f"=== run_backfill_historical_prices: {len(tickers)} ticker(s): {tickers} ===")

    market_data.init_db()

    from datetime import date, timedelta
    start = str(date.today() - timedelta(days=365 * 5))
    end = str(date.today())

    for db_ticker in tickers:
        alpaca_ticker = utils.to_alpaca_ticker(db_ticker)
        try:
            # backfill_ticker uses ticker for both API and DB — call fetch_bars directly
            # so we can translate the symbol for Alpaca but store under the DB ticker
            latest = market_data.get_latest_price_date(db_ticker)
            if latest and latest >= end:
                print(f"  [{db_ticker}] Already up to date ({latest}). Skipping.")
            else:
                effective_start = start
                if latest and latest > start:
                    from datetime import datetime as dt, timedelta as td
                    effective_start = str((dt.strptime(latest, "%Y-%m-%d") + td(days=1)).date())
                    print(f"  [{db_ticker}] Resuming from {effective_start} (Alpaca: {alpaca_ticker})")
                else:
                    print(f"  [{db_ticker}] Backfilling from {effective_start} (Alpaca: {alpaca_ticker})")
                rows = backfill_prices.fetch_bars(alpaca_ticker, effective_start, end)
                # Override ticker key so rows are stored under the DB ticker name
                for row in rows:
                    row["ticker"] = db_ticker
                if rows:
                    inserted = market_data.bulk_insert_prices(rows)
                    print(f"  [{db_ticker}] Inserted {inserted} rows ({rows[0]['date']} → {rows[-1]['date']})")
                else:
                    print(f"  [{db_ticker}] Nothing to insert.")
        except Exception as e:
            print(f"  [{db_ticker}] ERROR: {e}")
        time.sleep(SLEEP_BETWEEN)

    print("=== Done ===")
    market_data.status()
