"""Runner: scrape + analyze pending articles. Intended for Railway cron."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python_backend"))

import analysis

if __name__ == "__main__":
    print("=== run_analysis: run_pending_pipeline(limit=600) ===")
    analysis.run_pending_pipeline(limit=600)
    print("=== Done ===")
