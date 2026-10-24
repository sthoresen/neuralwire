"""
Runner: build the event timelines (event_pipeline). Run by scheduler.py every 4 hours.

  python run_events.py                   every active ticker
  python run_events.py --tickers NVDA,KO

Exits non-zero if a ticker failed, or if analyses were waiting and no model answered for any of
them, so the scheduler logs the run as failed instead of "completed OK".
"""

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python_backend"))

import database
import event_pipeline

# Analyses since the scan broke in March 2026 were marked checked without being scanned.
RESCAN = (
    "rescan_events_since_2026_03_27",
    "UPDATE analysis_runs SET event_check_status = 'pending' WHERE run_at >= '2026-03-27'",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", help="comma-separated; default: every active ticker")
    parser.add_argument("--batch", type=int, default=50, help="analyses to scan per ticker")
    args = parser.parse_args()

    database.migrate_add_event_check_status()
    database.migrate_add_event_candidates()
    database.apply_once(*RESCAN)

    tickers = args.tickers.split(",") if args.tickers else database.get_active_tickers()
    print(f"=== run_events: {len(tickers)} ticker(s) ===")
    total, failed = Counter(), []
    for ticker in tickers:
        try:
            stats = event_pipeline.run_event_pipeline(ticker, batch_size=args.batch)
        except Exception as e:
            print(f"[{ticker}] ERROR: {e}")
            failed.append(ticker)
            continue
        total.update(stats)
        print(
            f"[{ticker}] scanned {stats['scanned']}, flagged {stats['flagged']}, "
            f"no reply {stats['no_reply']} | months {stats['months']}, "
            f"events added {stats['events_added']}, unconfirmed {stats['unconfirmed']}, "
            f"too old {stats['too_old']}, repeats {stats['repeats']}, "
            f"months retried {stats['months_retried']}"
        )

    print(f"=== Done: {dict(total)} ===")
    if failed:
        print(f"Failed tickers: {failed}")
        return 1
    if total["no_reply"] and not total["scanned"]:
        print("No model answered any scan.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
