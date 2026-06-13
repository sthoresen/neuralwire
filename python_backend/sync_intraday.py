"""
sync_intraday.py — intraday (1-min bar) sync and pruning.

Resumes from the last stored timestamp — safe to run multiple times.
Used by run_sync_intraday.py (scheduled cron) and api.py (on-demand refresh).
"""

from datetime import date, timedelta, datetime as dt

import alpaca
import market_data
import utils


def sync_ticker_intraday(ticker: str) -> int:
    """
    Fetch 1-min bars from the last stored timestamp to today and upsert them.
    Returns the number of bars inserted.
    """
    latest = market_data.get_latest_intraday_timestamp(ticker)

    if latest:
        last_dt = dt.fromisoformat(str(latest).replace(" ", "T").rstrip("Z").split("+")[0])
        start = str((last_dt + timedelta(minutes=1)).date())
    else:
        start = str(date.today() - timedelta(days=5))

    end = str(date.today())

    bars = alpaca.get_bars(utils.to_alpaca_ticker(ticker), "1Min", start, end)
    if not bars:
        print(f"  [{ticker}] No intraday bars for {start} → {end}.")
        return 0

    rows = alpaca.bars_to_intraday_rows(ticker, bars)
    inserted = market_data.bulk_insert_intraday(rows)
    last_ts = rows[-1]["timestamp"]
    print(f"  [{ticker}] Upserted {inserted} bar(s) → {last_ts}")
    return inserted


def prune_old_intraday(days_keep: int = 7):
    """Delete intraday bars older than days_keep calendar days."""
    from db_connection import get_conn
    cutoff = str(date.today() - timedelta(days=days_keep))
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM intraday_prices WHERE timestamp < %s", (cutoff,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    print(f"  Pruned {deleted} intraday bar(s) older than {cutoff}.")
