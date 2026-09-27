"""Runner: scan analyzed articles for ticker events. Intended for Railway cron."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python_backend"))

import database
import event_pipeline

if __name__ == "__main__":
    tickers = database.get_active_tickers()
    print(f"=== run_events: {len(tickers)} ticker(s): {tickers} ===")

    for ticker in tickers:
        try:
            approved = event_pipeline.run_event_pipeline(ticker, batch_size=50, commit=True)
            print(f"[{ticker}] {len(approved)} new event(s) added")
        except Exception as e:
            print(f"[{ticker}] ERROR: {e}")

    print("=== Done ===")
