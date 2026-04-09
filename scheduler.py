"""
scheduler.py — NeuralWire background job scheduler.

Runs as a permanent Railway service alongside the web service.
Each job is launched as a subprocess so jobs are fully isolated from
each other and from this process.
"""
import subprocess
import sys
import os
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def run_script(script_path: str):
    """Launch a runner script as a subprocess and stream its output."""
    print(f"\n[scheduler] Starting: {script_path}", flush=True)
    result = subprocess.run(
        [sys.executable, script_path],
        cwd=BASE_DIR,
    )
    if result.returncode != 0:
        print(f"[scheduler] {script_path} exited with code {result.returncode}", flush=True)
    else:
        print(f"[scheduler] {script_path} completed OK", flush=True)


def job_fetch():
    run_script(os.path.join(BASE_DIR, "run_fetch.py"))

def job_analysis():
    run_script(os.path.join(BASE_DIR, "run_analysis.py"))

def job_earnings():
    run_script(os.path.join(BASE_DIR, "run_earnings.py"))

def job_sync_prices():
    run_script(os.path.join(BASE_DIR, "run_sync_daily_prices.py"))


if __name__ == "__main__":
    scheduler = BlockingScheduler(timezone="UTC")

    # Fetch new articles — every 2 hours
    scheduler.add_job(job_fetch, CronTrigger.from_crontab("0 */2 * * *"), id="fetch")

    # Analyze pending articles — every 30 minutes
    scheduler.add_job(job_analysis, CronTrigger.from_crontab("*/30 * * * *"), id="analysis")

    # Ingest earnings transcripts — daily at 06:00 UTC
    scheduler.add_job(job_earnings, CronTrigger.from_crontab("0 6 * * *"), id="earnings")

    # Sync daily prices — weekdays at 22:00 UTC (after NYSE close)
    scheduler.add_job(job_sync_prices, CronTrigger.from_crontab("0 22 * * 1-5"), id="sync_prices")

    print("[scheduler] Started. Jobs scheduled:", flush=True)
    for job in scheduler.get_jobs():
        print(f"  {job.id}", flush=True)

    scheduler.start()
