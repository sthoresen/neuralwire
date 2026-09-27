"""Runner: scrape + analyze pending articles. Runs as a continuous loop."""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python_backend"))

import analysis

BATCH_SIZE = 20  # small batch: the whole batch's full_text stays resident
# through the LLM phase, so this bounds the loop's footprint
COOLDOWN = 300  # seconds to idle, but only when there is nothing to do

if __name__ == "__main__":
    while True:
        print(f"=== run_analysis: run_pending_pipeline(limit={BATCH_SIZE}) ===", flush=True)
        stats = analysis.run_pending_pipeline(limit=BATCH_SIZE)

        if stats.batch_size and stats.advanced:
            # Backlog remains and we cleared articles out of it — keep going
            # rather than idling COOLDOWN between every small batch.
            continue

        if stats.batch_size:
            # Articles are pending but none advanced: every one hit no_content
            # or llm_error, both of which leave analysis_status='pending'. The
            # next query returns the same rows, so retrying immediately would
            # hot-loop against the LLM providers. Back off instead.
            print(
                f"=== {stats.batch_size} pending but none advanced "
                f"(llm_error={stats.llm_error}, no_content={stats.no_content}). "
                f"Backing off {COOLDOWN // 60} min... ===",
                flush=True,
            )
        else:
            print(f"=== Nothing pending. Sleeping {COOLDOWN // 60} minutes... ===", flush=True)

        time.sleep(COOLDOWN)
