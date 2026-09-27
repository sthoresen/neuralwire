# market_data.py
#
# Separate database tables for price/OHLCV data.
# Kept separate from the news/analysis tables because:
#   - Price data is high-volume time-series (different access patterns)
#   - Can be wiped/rebuilt independently without touching news analysis
#   - Clean domain separation
#
# Tables:
#   historical_prices — daily OHLCV bars (Alpaca)
#   intraday_prices   — minute bars, ~30 days rolling (Alpaca)

from db_connection import get_conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS historical_prices (
            id          SERIAL PRIMARY KEY,
            ticker      TEXT    NOT NULL,
            date        DATE    NOT NULL,
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            volume      INTEGER,
            vwap        REAL,
            trade_count INTEGER,
            UNIQUE(ticker, date)
        )
    """)

    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_prices_ticker_date
            ON historical_prices(ticker, date)
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS intraday_prices (
            id          SERIAL PRIMARY KEY,
            ticker      TEXT        NOT NULL,
            timestamp   TIMESTAMPTZ NOT NULL,
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            volume      INTEGER,
            vwap        REAL,
            trade_count INTEGER,
            UNIQUE(ticker, timestamp)
        )
    """)

    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_intraday_ticker_ts
            ON intraday_prices(ticker, timestamp)
    """)

    conn.commit()
    conn.close()
    print("market_data tables initialised.")


# ── historical_prices ─────────────────────────────────────────────────────────


def bulk_insert_prices(rows: list[dict]) -> int:
    """
    Bulk upsert OHLCV rows. Each dict must have:
        ticker, date, open, high, low, close, volume
    Optional: vwap, trade_count.
    Returns number of rows written.
    """
    if not rows:
        return 0
    conn = get_conn()
    c = conn.cursor()
    c.executemany(
        """
        INSERT INTO historical_prices
            (ticker, date, open, high, low, close, volume, vwap, trade_count)
        VALUES
            (%(ticker)s, %(date)s, %(open)s, %(high)s, %(low)s, %(close)s,
             %(volume)s, %(vwap)s, %(trade_count)s)
        ON CONFLICT(ticker, date) DO UPDATE SET
            open        = EXCLUDED.open,
            high        = EXCLUDED.high,
            low         = EXCLUDED.low,
            close       = EXCLUDED.close,
            volume      = EXCLUDED.volume,
            vwap        = EXCLUDED.vwap,
            trade_count = EXCLUDED.trade_count
    """,
        [{**{"vwap": None, "trade_count": None}, **r} for r in rows],
    )
    inserted = c.rowcount
    conn.commit()
    conn.close()
    return inserted


def get_prices(ticker: str, start_date: str = None, end_date: str = None) -> list[dict]:
    """Returns OHLCV rows for ticker, optionally filtered by date range (YYYY-MM-DD)."""
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()
    query = "SELECT * FROM historical_prices WHERE ticker = %s"
    params: list = [ticker]
    if start_date:
        query += " AND date >= %s"
        params.append(start_date)
    if end_date:
        query += " AND date <= %s"
        params.append(end_date)
    query += " ORDER BY date ASC"
    c.execute(query, params)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_latest_price_date(ticker: str) -> str | None:
    """Returns the most recent date we have prices for, or None."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT MAX(date) FROM historical_prices WHERE ticker = %s", (ticker,))
    row = c.fetchone()
    conn.close()
    return str(row[0]) if row and row[0] is not None else None


# ── intraday_prices ───────────────────────────────────────────────────────────


def bulk_insert_intraday(rows: list[dict]) -> int:
    """
    Bulk upsert minute-bar rows. Each dict must have:
        ticker, timestamp (ISO string), open, high, low, close, volume
    Optional: vwap, trade_count.
    """
    if not rows:
        return 0
    conn = get_conn()
    c = conn.cursor()
    c.executemany(
        """
        INSERT INTO intraday_prices
            (ticker, timestamp, open, high, low, close, volume, vwap, trade_count)
        VALUES
            (%(ticker)s, %(timestamp)s, %(open)s, %(high)s, %(low)s, %(close)s,
             %(volume)s, %(vwap)s, %(trade_count)s)
        ON CONFLICT(ticker, timestamp) DO UPDATE SET
            open        = EXCLUDED.open,
            high        = EXCLUDED.high,
            low         = EXCLUDED.low,
            close       = EXCLUDED.close,
            volume      = EXCLUDED.volume,
            vwap        = EXCLUDED.vwap,
            trade_count = EXCLUDED.trade_count
    """,
        [{**{"vwap": None, "trade_count": None}, **r} for r in rows],
    )
    inserted = c.rowcount
    conn.commit()
    conn.close()
    return inserted


def get_intraday(ticker: str, start: str = None, end: str = None) -> list[dict]:
    """Returns minute-bar rows ordered by timestamp ascending."""
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()
    query = "SELECT * FROM intraday_prices WHERE ticker = %s"
    params: list = [ticker]
    if start:
        query += " AND timestamp >= %s"
        params.append(start)
    if end:
        query += " AND timestamp <= %s"
        params.append(end)
    query += " ORDER BY timestamp ASC"
    c.execute(query, params)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_latest_intraday_timestamp(ticker: str) -> str | None:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT MAX(timestamp) FROM intraday_prices WHERE ticker = %s", (ticker,))
    row = c.fetchone()
    conn.close()
    return str(row[0]) if row and row[0] is not None else None


def status():
    """Print a quick health summary of the market_data tables."""
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()
    print(f"\n{'=' * 50}")
    print(" MARKET DATA")
    print(f"{'=' * 50}")
    for table, date_col in [("historical_prices", "date"), ("intraday_prices", "timestamp")]:
        c.execute(f"SELECT COUNT(*) FROM {table}")
        count = c.fetchone()[0]
        print(f"  {table}: {count} rows")
        c.execute(f"""
            SELECT ticker, COUNT(*) as n, MIN({date_col}) as first, MAX({date_col}) as last
            FROM {table} GROUP BY ticker ORDER BY ticker
        """)
        for r in c.fetchall():
            print(f"    {r['ticker']}: {r['n']}  ({r['first']} → {r['last']})")
    print(f"{'=' * 50}\n")
    conn.close()


if __name__ == "__main__":
    init_db()
    status()
