"""
One-shot migration: copies all data from the two SQLite databases into PostgreSQL.
Safe to run again — skips tables that already have data.
"""
import sqlite3

import psycopg2.extras

from db_connection import get_conn

FINANCIAL_DB = "financial_news_v2u.db"
MARKET_DB    = "market_data.db"


def migrate_table(sqlite_conn, pg_conn, table, columns, bool_cols=None):
    """
    Copy all rows from a SQLite table into PostgreSQL.
    Skips if the PG table already has rows (idempotent).
    bool_cols: list of column names that are stored as 0/1 in SQLite
               and need to be cast to Python bool for PostgreSQL.
    """
    pg_c = pg_conn.cursor()
    pg_c.execute(f"SELECT COUNT(*) FROM {table}")
    if pg_c.fetchone()[0] > 0:
        print(f"  {table}: already has data, skipping.")
        return

    sq_c = sqlite_conn.cursor()
    col_list = ", ".join(columns)
    sq_c.execute(f"SELECT {col_list} FROM {table}")
    rows = sq_c.fetchall()

    if not rows:
        print(f"  {table}: empty in SQLite, nothing to copy.")
        return

    if bool_cols:
        bool_idxs = {columns.index(c) for c in bool_cols if c in columns}
        rows = [
            tuple(bool(v) if i in bool_idxs and v is not None else v
                  for i, v in enumerate(row))
            for row in rows
        ]

    psycopg2.extras.execute_values(
        pg_c,
        f"INSERT INTO {table} ({col_list}) VALUES %s",
        rows,
        page_size=500,
    )
    pg_conn.commit()
    print(f"  {table}: copied {len(rows)} rows.")


def fix_serial_sequences(pg_conn):
    """
    After bulk inserting rows with explicit IDs, reset each SERIAL sequence
    so the next auto-generated ID doesn't collide.
    """
    tables_with_serial = [
        ("summaries",           "summaries_id_seq"),
        ("articles",            "articles_id_seq"),
        ("analysis_runs",       "analysis_runs_id_seq"),
        ("ticker_artefacts",    "ticker_artefacts_id_seq"),
        ("ticker_events",       "ticker_events_id_seq"),
        ("earnings",            "earnings_id_seq"),
        ("earnings_documents",  "earnings_documents_id_seq"),
        ("earnings_reactions",  "earnings_reactions_id_seq"),
        ("historical_prices",   "historical_prices_id_seq"),
        ("intraday_prices",     "intraday_prices_id_seq"),
    ]
    c = pg_conn.cursor()
    for table, seq in tables_with_serial:
        c.execute(f"SELECT MAX(id) FROM {table}")
        max_id = c.fetchone()[0]
        if max_id is not None:
            c.execute(f"SELECT setval('{seq}', {max_id})")
    pg_conn.commit()
    print("  Serial sequences reset.")


def main():
    pg_conn = get_conn()
    fin_conn = sqlite3.connect(FINANCIAL_DB)
    mkt_conn = sqlite3.connect(MARKET_DB)

    print("\n=== Migrating financial_news_v2u.db ===")

    migrate_table(fin_conn, pg_conn, "summaries", [
        "id", "ticker", "period_type", "period_value",
        "content", "short_content", "sources", "last_updated",
    ])
    migrate_table(fin_conn, pg_conn, "articles", [
        "id", "url", "ticker", "headline", "provider", "api_summary",
        "published_at", "scrape_status", "analysis_status", "scrape_error", "created_at",
    ])
    migrate_table(fin_conn, pg_conn, "article_content", [
        "article_id", "raw_full_text", "curated_text", "scraped_at",
    ])
    migrate_table(fin_conn, pg_conn, "analysis_runs", [
        "id", "article_id", "ticker", "model_name", "prompt_id", "prompt",
        "impact_headline", "reasoning", "ai_summary",
        "relevancy_score", "breaking_news_score", "importance_score",
        "sentiment_score", "run_at",
    ])
    migrate_table(fin_conn, pg_conn, "ticker_artefacts", [
        "id", "ticker", "artefact_type", "content",
        "model_name", "prompt_id", "prompt", "generated_at",
    ])
    migrate_table(fin_conn, pg_conn, "ticker_events", [
        "id", "ticker", "title", "event_date", "event_date_label",
        "description", "sources", "article_ids", "model_name", "created_at",
    ])
    migrate_table(fin_conn, pg_conn, "earnings", [
        "id", "ticker", "report_date", "period_label",
        "period_start", "period_end", "created_at",
    ])
    migrate_table(fin_conn, pg_conn, "earnings_documents", [
        "id", "earnings_id", "doc_type", "content", "quality_score",
        "is_preferred", "source_url", "source_article_id", "created_at",
    ], bool_cols=["is_preferred"])
    migrate_table(fin_conn, pg_conn, "earnings_reactions", [
        "id", "earnings_id", "beats_eps", "beats_revenue", "guidance_direction",
        "reaction_summary", "analyst_commentary",
        "contributing_article_ids", "model_name", "generated_at",
    ], bool_cols=["beats_eps", "beats_revenue"])
    migrate_table(fin_conn, pg_conn, "ticker_colors", [
        "ticker", "hex_color", "source", "updated_at",
    ])
    migrate_table(fin_conn, pg_conn, "ticker_classification", [
        "ticker", "long_name", "exchange", "gics_sector",
        "gics_industry", "display_tags", "fetched_at",
    ])

    print("\n=== Migrating market_data.db ===")

    migrate_table(mkt_conn, pg_conn, "historical_prices", [
        "id", "ticker", "date", "open", "high", "low",
        "close", "volume", "vwap", "trade_count",
    ])
    migrate_table(mkt_conn, pg_conn, "intraday_prices", [
        "id", "ticker", "timestamp", "open", "high", "low",
        "close", "volume", "vwap", "trade_count",
    ])

    print("\n=== Resetting sequences ===")
    fix_serial_sequences(pg_conn)

    fin_conn.close()
    mkt_conn.close()
    pg_conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    main()
