"""Runner: scrape + analyze pending articles in a loop, one batch per subprocess."""

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python_backend"))

BATCH_SIZE = 20  # small batch: the whole batch's full_text stays resident
# through the LLM phase, so this bounds the batch's footprint
COOLDOWN = 300  # seconds to idle, but only when there is nothing to do
RETRY = 60  # seconds to wait after a database error or a crashed batch

# Batch process exit codes
CONTINUE = 0
IDLE = 10
DB_UNAVAILABLE = 11


def run_batch() -> int:
    """Process one batch of pending articles and return an exit code."""
    import psycopg2

    import analysis

    print(f"=== run_analysis: run_pending_pipeline(limit={BATCH_SIZE}) ===", flush=True)
    try:
        stats = analysis.run_pending_pipeline(limit=BATCH_SIZE)
    except psycopg2.OperationalError as e:
        print(f"=== Database unavailable, retrying in {RETRY}s: {e} ===", flush=True)
        return DB_UNAVAILABLE

    if stats.batch_size and stats.advanced:
        # Backlog remains and we cleared articles out of it — keep going
        # rather than idling COOLDOWN between every small batch.
        return CONTINUE

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
    return IDLE


def wait_after(exit_code: int) -> int:
    """Seconds to wait after a batch process exits with `exit_code`."""
    if exit_code == CONTINUE:
        return 0
    if exit_code == IDLE:
        return COOLDOWN
    if exit_code == DB_UNAVAILABLE:
        return RETRY
    print(f"=== Batch crashed (exit code {exit_code}), retrying in {RETRY}s ===", flush=True)
    return RETRY


def run_forever() -> None:
    """Run each batch in a fresh process, so its memory is freed when it exits."""
    while True:
        batch = subprocess.run([sys.executable, os.path.abspath(__file__), "--once"])
        if delay := wait_after(batch.returncode):
            time.sleep(delay)


if __name__ == "__main__":
    if "--once" in sys.argv:
        sys.exit(run_batch())
    run_forever()
