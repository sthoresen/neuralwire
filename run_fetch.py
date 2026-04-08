"""Runner: fetch new articles from all providers. Intended for Railway cron."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python_backend"))

import news_reporter

if __name__ == "__main__":
    print("=== run_fetch: pull_all_news ===")
    news_reporter.pull_all_news()
    print("=== Done ===")
