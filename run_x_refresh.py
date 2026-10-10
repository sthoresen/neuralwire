"""Runner: the X pipeline's scheduled refresh, run from scheduler.py.

  python run_x_refresh.py                 # fetch, score, rewrite the 24-hour briefings if changed
  python run_x_refresh.py --weekly        # also the 7-day briefings and the 30-day text purge
  python run_x_refresh.py --lookback 168  # fetch up to 7 days back (a first run, or after a gap)
  python run_x_refresh.py --force         # rewrite the briefings even if their posts are the same
  python run_x_refresh.py --report        # print what each query fetched
  python run_x_refresh.py --rebuild       # local DB only: reload everything from data/x_raw/

1. Fetch: each Apify query (one per group of accounts) asks for the posts since its last
   successful fetch ended, so no post is paid for twice. Where X's free counts reach, the
   same window is counted there too, to show what the fetch missed.
2. Score: posts without Jev labels for the current question set get them.
3. Write: per section, the briefing on the last 24 hours is rewritten only when the posts it
   would be written from differ from the last briefing's. With --weekly, the 7-day ones too.
"""

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python_backend"))

import x_digest
import x_jev
import x_sources
import x_store
from x_accounts import ACCOUNTS, GROUPS

JOB = "refresh"
LOOKBACK_HOURS = 26  # how far back a query with no earlier fetch (or a long gap) starts
MIN_WINDOW = timedelta(minutes=5)  # skip a fetch when the last one was this recent
BUDGET_USD = 0.25  # per run; a normal refresh costs about $0.02
TEXT_DAYS = 30  # post text is kept this long (X's rules); see x_store.purge_old_text


class BudgetExceeded(RuntimeError):
    pass


def fetch_window(label: str, now: datetime, lookback: timedelta) -> tuple[datetime, datetime]:
    """From where this query's last successful fetch ended, but never more than `lookback`."""
    last = x_store.last_fetched_until(label)
    earliest = now - lookback
    return (max(last, earliest) if last else earliest), now


def fetch(now: datetime, lookback: timedelta, spent: list[float], budget: float) -> list[dict]:
    """Fetch every group's new posts. Returns one summary row per query, for the report."""
    rows = []
    days = max(lookback / timedelta(days=1), 1)
    for group in GROUPS:
        handles = [a.handle for a in ACCOUNTS if a.group == group]
        for i, chunk in enumerate(x_sources.chunk_handles(handles), start=1):
            label = f"{group}{i}"
            start, end = fetch_window(label, now, lookback)
            if end - start < MIN_WINDOW:
                continue
            if spent[0] >= budget:
                raise BudgetExceeded(f"spent ${spent[0]:.4f} of the ${budget:.2f} budget")
            row = {"query": label, "start": start, "end": end, "expected": None}
            try:
                rec, replayed = x_sources.fetch_apify(
                    chunk,
                    start,
                    end,
                    label,
                    JOB,
                    max_items=int(300 * days),
                    max_charge_usd=round(0.10 * days, 2),
                )
            except RuntimeError as e:
                print(f"  [{label}] ERROR: {e}")
                meta = {
                    "job": JOB,
                    "source": "apify",
                    "label": label,
                    "query": x_sources.apify_query(chunk, start, end),
                    "window_start": start,
                    "window_end": end,
                }
                x_store.record_run(meta, 0, 0, 0, replayed=False, error=str(e))
                rows.append({**row, "got": None, "mock": None, "cost": 0.0})
                continue
            posts, stats = x_sources.parse_apify_items(rec["items"], start, end)
            run_id = x_store.record_run(
                rec["meta"], len(posts), stats["mock"], stats["dropped"], replayed
            )
            x_store.upsert_posts(posts, run_id)
            cost = 0.0 if replayed else rec["meta"]["est_cost_usd"]
            spent[0] += cost
            if len(rec["items"]) >= rec["meta"]["max_items"]:
                print(f"  [{label}] WARNING: hit max_items, posts may be missing")
            row.update(got=len(posts), mock=stats["mock"], cost=cost)

            if x_sources.counts_in_reach(start):
                try:
                    crec, creplayed = x_sources.count_x(chunk, start, end, label, JOB)
                    x_store.record_run(crec["meta"], crec["total"], 0, 0, creplayed)
                    row["expected"] = crec["total"]
                except RuntimeError as e:
                    print(f"  [{label}] counts check skipped: {e}")
            rows.append(row)
    return rows


def score(now: datetime, lookback: timedelta, spent: list[float], budget: float) -> dict:
    """Jev labels for every recent post that has none for x_jev.QSET."""
    start = now - max(lookback, timedelta(days=2))
    posts = x_store.posts_without_labels(start, now, x_jev.QSET)
    if not posts:
        return {"labelled": 0, "failed": 0, "cost": 0.0}
    if spent[0] >= budget:
        raise BudgetExceeded(f"spent ${spent[0]:.4f} of the ${budget:.2f} budget")
    try:
        rec = x_jev.label_posts(posts, start, now, "labels", JOB)
    except RuntimeError as e:
        print(f"  [jev] skipped: {e}")
        return {"labelled": 0, "failed": len(posts), "cost": 0.0}
    meta = rec["meta"]
    run_id = x_store.record_run(meta, len(rec["labels"]), 0, len(rec["failed"]), False)
    x_store.save_labels(rec["labels"], x_jev.QSET, run_id)
    spent[0] += meta["est_cost_usd"]
    return {
        "labelled": len(rec["labels"]),
        "failed": len(rec["failed"]),
        "cost": meta["est_cost_usd"],
    }


def _save(digest: dict | None, section: str, period: str) -> None:
    if digest is None:
        print(f"  [{section} {period}] no posts to write from")
        return
    x_store.save_digest(digest)
    c = digest["content"]
    words = len(x_digest.plain_text(c).split())
    print(f"  [{section} {period}] {c['title']!r}: {words} words ({digest['model_name']})")


def write(now: datetime, weekly: bool, force: bool) -> None:
    """Rewrite each section's 24-hour briefing if its posts changed; with weekly, the 7-day too."""
    for section in x_digest.SECTIONS:
        candidates = x_digest.daily_candidates(section, now)
        ids = {int(p["post_id"]) for p in candidates[0] + candidates[1]}
        previous = x_store.latest_digest(section, "day")
        if not force and previous and set(previous["candidate_ids"]) == ids:
            print(
                f"  [{section} day] same posts as the briefing at {previous['edition_at']:%H:%M} UTC, kept"
            )
            continue
        try:
            _save(x_digest.write_daily(section, now, candidates=candidates), section, "day")
        except RuntimeError as e:
            print(f"  [{section} day] ERROR: {e}")
    if weekly:
        for section in x_digest.SECTIONS:
            try:
                _save(x_digest.write_weekly(section, now), section, "week")
            except RuntimeError as e:
                print(f"  [{section} week] ERROR: {e}")


def rebuild() -> None:
    """Reload posts, runs, Jev labels and judgments from the raw archive. No API calls."""
    if not x_store.is_local_db():
        sys.exit("--rebuild only runs against a local database")
    files = x_sources.archive_files()
    print(f"=== rebuild: {len(files)} archived response(s) from {x_sources.RAW_DIR} ===")
    x_store.clear_for_rebuild()
    for path in files:
        rec = x_sources.read_archive(path)
        meta = rec["meta"]
        if meta["source"] == "apify":
            start = datetime.fromisoformat(meta["window_start"])
            end = datetime.fromisoformat(meta["window_end"])
            posts, stats = x_sources.parse_apify_items(rec["items"], start, end)
            # The original fetch, so its original cost: replayed=False.
            run_id = x_store.record_run(meta, len(posts), stats["mock"], stats["dropped"], False)
            x_store.upsert_posts(posts, run_id)
        elif meta["source"] == "jev":
            run_id = x_store.record_run(meta, len(rec["labels"]), 0, len(rec["failed"]), False)
            x_store.save_labels(rec["labels"], meta["qset"], run_id)
        else:
            x_store.record_run(meta, rec["total"], 0, 0, False)
    judgments = x_sources.latest_judgments()
    for post_id, verdict in judgments.items():
        x_store.set_judgment(post_id, verdict)
    print(f"=== Done: {len(judgments)} judgment(s) replayed ===")


def print_report(rows: list[dict]) -> None:
    print("\nQueries (expected = X's free count of the same posts; '-' = out of its 7-day reach)")
    print(
        f"  {'query':<8}{'from (UTC)':<18}{'to':<18}{'got':>6}{'expected':>10}{'mock':>6}{'cost $':>9}"
    )
    for r in rows:
        exp = "-" if r["expected"] is None else r["expected"]
        got = "ERR" if r["got"] is None else r["got"]
        print(
            f"  {r['query']:<8}{r['start']:%m-%d %H:%M}{'':<7}{r['end']:%m-%d %H:%M}{'':<7}"
            f"{got:>6}{exp:>10}{r['mock'] or 0:>6}{r['cost']:>9.4f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--weekly", action="store_true", help="also the 7-day briefings and the purge"
    )
    parser.add_argument("--lookback", type=float, default=LOOKBACK_HOURS, metavar="HOURS")
    parser.add_argument("--force", action="store_true", help="rewrite briefings even if unchanged")
    parser.add_argument("--no-write", action="store_true", help="fetch and score only")
    parser.add_argument("--report", action="store_true", help="print per-query results")
    parser.add_argument("--rebuild", action="store_true", help="reload the DB from the archive")
    args = parser.parse_args()

    x_store.init_tables()
    x_store.sync_accounts(ACCOUNTS)
    if args.rebuild:
        rebuild()
        return

    now = datetime.now(UTC).replace(second=0, microsecond=0)
    lookback = timedelta(hours=args.lookback)
    budget = BUDGET_USD * max(lookback / timedelta(days=1), 1)
    print(
        f"=== run_x_refresh: {now:%Y-%m-%d %H:%M} UTC, lookback {args.lookback:g}h, budget ${budget:.2f} ==="
    )

    spent, rows = [0.0], []
    try:
        rows = fetch(now, lookback, spent, budget)
        lab = score(now, lookback, spent, budget)
        print(
            f"  fetched {sum(r['got'] or 0 for r in rows)} posts, scored {lab['labelled']} (${spent[0]:.4f})"
        )
    except BudgetExceeded as e:
        print(f"STOPPED: {e}")
        return
    if not args.no_write:
        write(now, args.weekly, args.force)
    if args.weekly:
        print(
            f"  purged the text of {x_store.purge_old_text(TEXT_DAYS)} posts older than {TEXT_DAYS} days"
        )
        x_sources.purge_archive(TEXT_DAYS)
    if args.report:
        print_report(rows)
    print("=== Done ===")


if __name__ == "__main__":
    main()
