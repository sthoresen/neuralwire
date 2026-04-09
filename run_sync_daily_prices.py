"""Runner: sync yesterday's daily OHLCV bars from Alpaca for all active tickers."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python_backend"))

from db_connection import get_conn
from alpaca_trade_api.rest import REST
import market_data
import sync_prices
import utils
from datetime import date, timedelta
from alpaca_trade_api.rest import TimeFrame

SLEEP_BETWEEN = 0.5


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
    tickers = get_active_tickers()
    print(f"=== run_sync_daily_prices: {len(tickers)} ticker(s): {tickers} ===")

    market_data.init_db()

    api = REST(
        key_id=utils.get_env_variable("ALPACA_KEY"),
        secret_key=utils.get_env_variable("ALPACA_SECRET"),
        base_url="https://paper-api.alpaca.markets/v2",
    )

    for db_ticker in tickers:
        alpaca_ticker = utils.to_alpaca_ticker(db_ticker)
        try:
            # Fetch from Alpaca using the translated symbol, store under the DB ticker
            latest = market_data.get_latest_price_date(db_ticker)
            end = str(date.today() - timedelta(days=1))

            if latest is None:
                print(f"  [{db_ticker}] No data — run run_backfill_historical_prices.py first.")
            elif latest >= end:
                print(f"  [{db_ticker}] Already up to date ({latest}).")
            else:
                from datetime import datetime as dt
                start = str((dt.strptime(latest, "%Y-%m-%d") + timedelta(days=1)).date())
                print(f"  [{db_ticker}] Syncing {start} → {end} (Alpaca: {alpaca_ticker})...")
                bars = api.get_bars(
                    alpaca_ticker, TimeFrame.Day,
                    start=start, end=end,
                    adjustment="split", feed="iex", limit=1000,
                ).df
                if bars.empty:
                    print(f"  [{db_ticker}] No new bars.")
                else:
                    rows = [{
                        "ticker": db_ticker,
                        "date": str(ts.date()),
                        "open": float(row["open"]), "high": float(row["high"]),
                        "low": float(row["low"]), "close": float(row["close"]),
                        "volume": int(row["volume"]),
                        "vwap": float(row["vwap"]) if "vwap" in row else None,
                        "trade_count": int(row["trade_count"]) if "trade_count" in row else None,
                    } for ts, row in bars.iterrows()]
                    inserted = market_data.bulk_insert_prices(rows)
                    print(f"  [{db_ticker}] Inserted {inserted} row(s) → {rows[-1]['date']}")
        except Exception as e:
            print(f"  [{db_ticker}] ERROR: {e}")
        time.sleep(SLEEP_BETWEEN)

    print("=== Done ===")
    market_data.status()
