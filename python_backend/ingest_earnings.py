"""
ingest_earnings.py

Fetches earnings call transcripts from defeatbeta-api and stores them in the
earnings + earnings_documents tables.

Usage:
    python ingest_earnings.py                             # dry run, NVDA, last 4
    python ingest_earnings.py --commit                    # actually insert
    python ingest_earnings.py --ticker AAPL --limit 8 --commit
    python ingest_earnings.py --fiscal-year 2025 --fiscal-quarter 4 --commit
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import database

DEFAULT_TICKER = "NVDA"
DEFAULT_LIMIT = 4  # most recent quarters


# ── Helpers ───────────────────────────────────────────────────────────────────


def _period_label(fiscal_year: int, fiscal_quarter: int) -> str:
    return f"Q{fiscal_quarter} FY{fiscal_year}"


def _format_transcript(df) -> str:
    """
    Converts the paragraph DataFrame into a readable plain-text transcript.
    Format:
        [Speaker Name]
        paragraph text...

        [Next Speaker]
        next paragraph...
    """
    lines = []
    for _, row in df.iterrows():
        speaker = str(row.get("speaker") or "").strip()
        content = str(row.get("content") or "").strip()
        if speaker:
            lines.append(f"[{speaker}]")
        if content:
            lines.append(content)
        lines.append("")  # blank line between paragraphs
    return "\n".join(lines).strip()


def _already_has_transcript(earnings_id: int) -> bool:
    """Returns True if a transcript document already exists for this earnings event."""
    docs = database.get_earnings_documents(earnings_id, doc_type="transcript")
    return len(docs) > 0


# ── Core fetch + save ─────────────────────────────────────────────────────────


def fetch_and_save(
    ticker: str, fiscal_year: int, fiscal_quarter: int, report_date: str, commit: bool
) -> bool:
    """
    Fetches the transcript for one quarter and saves it to the DB.
    Returns True if a new document was saved (or would be in dry-run).
    """
    period_label = _period_label(fiscal_year, fiscal_quarter)
    print(f"  {period_label} (report_date={report_date}) ...", end=" ", flush=True)

    # --- Check if we already have this in DB ---
    if commit:
        existing_earnings_id = None
        rows = database.get_earnings_list(ticker, limit=200)
        for r in rows:
            if str(r["report_date"]) == report_date:
                existing_earnings_id = r["id"]
                break

        if existing_earnings_id and _already_has_transcript(existing_earnings_id):
            print("already in DB, skip")
            return False

    # --- Fetch transcript from defeatbeta ---
    try:
        from defeatbeta_api.data.ticker import Ticker

        obj = Ticker(ticker).earning_call_transcripts()
        df = obj.get_transcript(fiscal_year=fiscal_year, fiscal_quarter=fiscal_quarter)
    except Exception as e:
        print(f"FETCH ERROR: {e}")
        return False

    if df is None or df.empty:
        print("no transcript data returned, skip")
        return False

    content = _format_transcript(df)
    word_count = len(content.split())
    print(f"{word_count} words", end=" ")

    if not commit:
        print("[dry run]")
        return True

    # --- Upsert earnings anchor ---
    earnings_id = database.get_or_create_earnings(
        ticker=ticker,
        report_date=report_date,
        period_label=period_label,
    )
    if not earnings_id:
        print("ERROR: could not create earnings row")
        return False

    # --- Save transcript document ---
    doc_id = database.save_earnings_document(
        earnings_id=earnings_id,
        doc_type="transcript",
        content=content,
        quality_score=90,  # structured, line-by-line source
        is_preferred=True,
        source_url=None,
        source_article_id=None,
    )

    if doc_id:
        print(f"-> saved (earnings_id={earnings_id}, doc_id={doc_id})")
        return True
    else:
        print("ERROR: document save failed")
        return False


# ── Main pipeline ─────────────────────────────────────────────────────────────


def run(ticker: str, limit: int, fiscal_year: int | None, fiscal_quarter: int | None, commit: bool):

    print(f"\nTicker : {ticker}")
    print(f"Mode   : {'COMMIT' if commit else 'DRY RUN'}")

    # Fetch transcript metadata list
    try:
        from defeatbeta_api.data.ticker import Ticker

        meta_df = Ticker(ticker).earning_call_transcripts().get_transcripts_list()
    except Exception as e:
        print(f"ERROR fetching transcript list: {e}")
        sys.exit(1)

    # Filter to a single quarter if specified
    if fiscal_year and fiscal_quarter:
        meta_df = meta_df[
            (meta_df["fiscal_year"] == fiscal_year) & (meta_df["fiscal_quarter"] == fiscal_quarter)
        ]
        if meta_df.empty:
            print(f"No transcript found for Q{fiscal_quarter} FY{fiscal_year}")
            sys.exit(0)
    else:
        # Most recent N quarters, newest first
        meta_df = meta_df.sort_values("report_date", ascending=False).head(limit)

    total = len(meta_df)
    print(f"Quarters to process: {total}\n")

    saved = 0
    for _, row in meta_df.iterrows():
        ok = fetch_and_save(
            ticker=ticker,
            fiscal_year=int(row["fiscal_year"]),
            fiscal_quarter=int(row["fiscal_quarter"]),
            report_date=str(row["report_date"]),
            commit=commit,
        )
        if ok:
            saved += 1

    print(f"\nDone. {saved}/{total} transcripts {'saved' if commit else 'would be saved'}.")
    if not commit:
        print("Pass --commit to write to DB.")
    return saved


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest earnings transcripts via defeatbeta-api")
    parser.add_argument("--ticker", default=DEFAULT_TICKER, help="Ticker symbol (default: NVDA)")
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT, help="Most recent N quarters (default: 4)"
    )
    parser.add_argument(
        "--fiscal-year", type=int, default=None, help="Fetch a single specific fiscal year"
    )
    parser.add_argument(
        "--fiscal-quarter",
        type=int,
        default=None,
        choices=[1, 2, 3, 4],
        help="Fetch a single specific fiscal quarter",
    )
    parser.add_argument("--commit", action="store_true", help="Write to DB (default is dry run)")
    args = parser.parse_args()

    if bool(args.fiscal_year) != bool(args.fiscal_quarter):
        parser.error("--fiscal-year and --fiscal-quarter must be used together")

    run(
        ticker=args.ticker,
        limit=args.limit,
        fiscal_year=args.fiscal_year,
        fiscal_quarter=args.fiscal_quarter,
        commit=args.commit,
    )
