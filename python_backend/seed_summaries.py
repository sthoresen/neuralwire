"""
Seed the summaries table from agentic_data/<TICKER>/ folders.

Runs idempotently: skips any ticker that already has rows in summaries.
Scans every subdirectory of AGENTIC_DATA_DIR and inserts:
  - entire_history_summary.md  -> period_type='master',      period_value='entire_history'
  - YYYY-YYYY.md (long span)   -> period_type='early_years', period_value='YYYY-YYYY'
  - YYYY-YYYY.md (5-yr span)   -> period_type='five_year',   period_value='YYYY-YYYY'
  - YYYY.json                  -> period_type='yearly',       period_value='YYYY'
"""

import json
import os
import re

from db_connection import get_conn

AGENTIC_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agentic_data")


def get_existing_tickers(conn):
    rows = conn.execute("SELECT DISTINCT ticker FROM summaries").fetchall()
    return {r[0] for r in rows}


def classify_md_file(filename):
    """Return period_type for a YYYY-YYYY.md file based on the year span."""
    m = re.match(r"(\d{4})-(\d{4})\.md$", filename)
    if not m:
        return None, None
    start, end = int(m.group(1)), int(m.group(2))
    period_value = f"{m.group(1)}-{m.group(2)}"
    # A "five_year" block covers exactly 5 calendar years (end - start == 4).
    # Anything longer is treated as early_years.
    period_type = "five_year" if (end - start) == 4 else "early_years"
    return period_type, period_value


def insert_ticker(conn, ticker, folder):
    rows = []

    for fname in os.listdir(folder):
        fpath = os.path.join(folder, fname)

        # --- entire_history_summary.md ---
        if fname == "entire_history_summary.md":
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            rows.append((ticker, "master", "entire_history", content, None, None))

        # --- YYYY-YYYY.md ---
        elif re.match(r"\d{4}-\d{4}\.md$", fname):
            period_type, period_value = classify_md_file(fname)
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            rows.append((ticker, period_type, period_value, content, None, None))

        # --- YYYY.json ---
        elif re.match(r"\d{4}\.json$", fname):
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            period_value = fname.replace(".json", "")
            content = data.get("extended", "")
            short_content = data.get("short", None)
            sources = json.dumps(data["sources"]) if data.get("sources") else None
            rows.append((ticker, "yearly", period_value, content, short_content, sources))

    if not rows:
        print(f"  [{ticker}] No files found, skipping.")
        return

    c = conn.cursor()
    c.executemany(
        """
        INSERT INTO summaries (ticker, period_type, period_value, content, short_content, sources)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        rows,
    )
    print(f"  [{ticker}] Inserted {len(rows)} rows.")


def main():
    if not os.path.isdir(AGENTIC_DATA_DIR):
        print(f"agentic_data directory not found: {AGENTIC_DATA_DIR}")
        return

    conn = get_conn()
    existing = get_existing_tickers(conn)

    tickers_in_dir = sorted(
        d for d in os.listdir(AGENTIC_DATA_DIR) if os.path.isdir(os.path.join(AGENTIC_DATA_DIR, d))
    )

    new_tickers = [t for t in tickers_in_dir if t not in existing]
    skipped = [t for t in tickers_in_dir if t in existing]

    if skipped:
        print(f"Already in DB, skipping: {', '.join(skipped)}")

    if not new_tickers:
        print("Nothing new to insert.")
        conn.close()
        return

    print(f"Inserting {len(new_tickers)} new ticker(s): {', '.join(new_tickers)}")
    for ticker in new_tickers:
        folder = os.path.join(AGENTIC_DATA_DIR, ticker)
        insert_ticker(conn, ticker, folder)

    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
