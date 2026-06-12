"""Runner: scrape + analyze pending articles. Runs as a continuous loop."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python_backend"))

import analysis

COOLDOWN = 300  # seconds between runs

if __name__ == "__main__":
    while True:
        print("=== run_analysis: run_pending_pipeline(limit=200) ===", flush=True)
        analysis.run_pending_pipeline(limit=200)
        print(f"=== Done. Sleeping {COOLDOWN // 60} minutes... ===", flush=True)
        time.sleep(COOLDOWN)
