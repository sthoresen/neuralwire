"""
Ingest a bootstrapped event list from a JSON file into ticker_events.

Usage:
    python ingest_events.py                        # dry run
    python ingest_events.py --commit               # actually insert
    python ingest_events.py --commit --force       # re-insert even if rows exist
    python ingest_events.py --file path/to/file.txt --ticker NVDA --commit
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import database

DEFAULT_FILE   = os.path.join(os.path.dirname(__file__), "../nvidia ai data/events/megapasta.txt")
DEFAULT_TICKER = "NVDA"

# Stored verbatim in the model_name column so bootstrap rows are clearly identifiable.
BOOTSTRAP_MODEL = "claude-agentic-v1 [bootstrap]"


def load_and_validate(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("Expected a JSON array at the top level.")

    errors = []
    for i, ev in enumerate(data):
        if not ev.get("title"):
            errors.append(f"  [{i}] Missing 'title'")
        if not ev.get("description"):
            errors.append(f"  [{i}] Missing 'description' — title: {ev.get('title')}")

    if errors:
        print(f"Validation issues ({len(errors)}):")
        for e in errors:
            print(e)

    return data, errors


def preview(events, n=5):
    print(f"\nTotal events loaded: {len(events)}")
    print(f"\nFirst {n} events:")
    for ev in events[:n]:
        print(f"  [{ev.get('event_date_label', '?')}] {ev.get('title')}")
    print(f"  ...")
    print(f"\nLast {n} events:")
    for ev in events[-n:]:
        print(f"  [{ev.get('event_date_label', '?')}] {ev.get('title')}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file",   default=DEFAULT_FILE,   help="Path to JSON events file")
    parser.add_argument("--ticker", default=DEFAULT_TICKER, help="Ticker symbol (default: NVDA)")
    parser.add_argument("--commit", action="store_true",    help="Actually insert (default is dry run)")
    parser.add_argument("--force",  action="store_true",    help="Insert even if events already exist")
    args = parser.parse_args()

    print(f"File:   {args.file}")
    print(f"Ticker: {args.ticker}")
    print(f"Model:  {BOOTSTRAP_MODEL}")
    print(f"Mode:   {'COMMIT' if args.commit else 'DRY RUN'}")

    events, errors = load_and_validate(args.file)
    preview(events)

    if errors and args.commit:
        print(f"\n{len(errors)} validation error(s) found. Fix them or re-run to insert anyway.")
        sys.exit(1)

    if not args.commit:
        print("\nDry run complete. Pass --commit to insert.")
        return

    database.migrate_add_earnings_tables()

    inserted = database.bulk_insert_ticker_events(
        ticker=args.ticker,
        events=events,
        model_name=BOOTSTRAP_MODEL,
        skip_if_exists=not args.force,
    )
    print(f"\nDone. {inserted} rows inserted.")


if __name__ == "__main__":
    main()
