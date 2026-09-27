"""
event_pipeline.py

Two-pass pipeline that scans analysis_runs for new events and appends them
to ticker_events.

Pass 1 — cheap model (per article):
    Reads each unscanned analysis_run and asks a cheap model if the article
    describes a new major event. Returns a candidate or nothing.
    All scanned IDs are marked 'checked' immediately after the pass.

Pass 2 — expensive model (batch):
    Receives all candidates + the full existing event list and decides which
    ones are genuinely worth adding. Also cleans up titles/descriptions.

Usage:
    python event_pipeline.py              # dry run (no DB writes)
    python event_pipeline.py --commit     # write new events to DB
    python event_pipeline.py --ticker AMD --batch 100 --commit
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import database
import llms
import prompts
import utils

# ── Formatting helpers ────────────────────────────────────────────────────────


def _format_existing_short(events):
    """Condensed list for the cheap scan prompt (date + title per line)."""
    if not events:
        return "(none yet)"
    lines = [f"{e.get('event_date', '?')[:7]} | {e['title']}" for e in events]
    return "\n".join(lines)


def _format_existing_full(events):
    """Full list for the expensive validate prompt."""
    if not events:
        return "(none yet)"
    lines = []
    for e in events:
        lines.append(
            f"[{e.get('event_date_label') or e.get('event_date', '?')}] "
            f"{e['title']} — {e['description']}"
        )
    return "\n".join(lines)


# ── Pass 1: cheap scan ────────────────────────────────────────────────────────


def _scan_one(ar, existing_summary, llm_manager):
    """
    Asks the cheap model whether a single analysis_run contains a new event.
    Returns a candidate dict or None.
    """
    prompt = prompts.event_scan_prompt.format(
        ticker=ar["ticker"],
        existing_events_summary=existing_summary,
        published_at=ar.get("published_at", "unknown"),
        headline=ar.get("headline", ""),
        impact_headline=ar.get("impact_headline", ""),
        ai_summary=ar.get("ai_summary", ""),
    )

    raw, model_used = llm_manager.call(prompt, tier="economy", reasoning=False, max_tokens=500)
    if not raw:
        print(f"    [scan] LLM unavailable for analysis_run {ar['id']}")
        return None

    data = utils.clean_json_response(raw)
    if not isinstance(data, dict):
        return None

    if not data.get("is_event"):
        return None

    return {
        "title": data.get("title", "").strip(),
        "event_date": data.get("event_date"),
        "event_date_label": data.get("event_date_label"),
        "description": data.get("description", "").strip(),
        "source_article_ids": [ar["article_id"]],
        "_analysis_id": ar["id"],
        "_model": model_used,
    }


def run_scan_pass(ticker, analyses, existing_events, llm_manager):
    """
    Runs the cheap scan over all supplied analyses.
    Returns (candidates, checked_ids).
    """
    existing_summary = _format_existing_short(existing_events)
    candidates = []
    checked_ids = []
    total = len(analyses)

    print(f"\n[Pass 1 — scan] {total} analyses to check")

    for i, ar in enumerate(analyses):
        print(f"  [{i + 1}/{total}] article_id={ar['article_id']} ...", end=" ", flush=True)
        candidate = _scan_one(ar, existing_summary, llm_manager)
        checked_ids.append(ar["id"])

        if candidate:
            print(f"CANDIDATE: {candidate['title'][:60]}")
            candidates.append(candidate)
        else:
            print("skip")

    print(f"\n[Pass 1 done] {len(candidates)} candidates from {total} analyses")
    return candidates, checked_ids


# ── Pass 2: expensive validation ─────────────────────────────────────────────


def run_validate_pass(ticker, candidates, existing_events, llm_manager):
    """
    Sends all candidates to the expensive model for final curation.
    Returns a list of approved event dicts ready for DB insertion.
    """
    if not candidates:
        return []

    existing_full = _format_existing_full(existing_events)
    candidates_json = json.dumps(candidates, indent=2)

    prompt = prompts.event_validate_prompt.format(
        ticker=ticker,
        existing_events=existing_full,
        candidates_json=candidates_json,
    )

    print(f"\n[Pass 2 — validate] Sending {len(candidates)} candidates to expensive model...")

    raw, model_used = llm_manager.call(prompt, tier="standard", reasoning=False, max_tokens=3000)
    if not raw:
        print("[Pass 2] LLM unavailable — no events added")
        return []

    approved = utils.clean_json_response(raw)
    if not isinstance(approved, list):
        print(f"[Pass 2] Unexpected response shape: {type(approved)}")
        return []

    for ev in approved:
        ev["_model"] = model_used

    print(f"[Pass 2 done] {len(approved)} events approved")
    return approved


# ── Main pipeline ─────────────────────────────────────────────────────────────


def run_event_pipeline(ticker, batch_size=50, commit=True):
    """
    Full pipeline: scan → validate → insert.

    commit=False runs both model passes but skips all DB writes (dry run).
    Returns the list of approved events.
    """
    database.migrate_add_event_check_status()

    llm_manager = llms.LLMProviderManager()

    # 1. Fetch unscanned analyses
    analyses = database.get_unchecked_analyses(ticker, limit=batch_size)
    if not analyses:
        print(f"[{ticker}] No unscanned analyses found.")
        return []

    # 2. Fetch existing events for dedup context
    existing_events = database.get_ticker_events(ticker)
    print(f"[{ticker}] {len(existing_events)} existing events, {len(analyses)} analyses to scan")

    # 3. Cheap scan pass
    candidates, checked_ids = run_scan_pass(ticker, analyses, existing_events, llm_manager)

    # Mark as checked regardless of commit flag — we've done the work
    if commit:
        database.mark_analyses_event_checked(checked_ids)
        print(f"Marked {len(checked_ids)} analyses as checked")
    else:
        print(f"[dry run] Would mark {len(checked_ids)} analyses as checked")

    # 4. Expensive validation pass
    approved = run_validate_pass(ticker, candidates, existing_events, llm_manager)

    if not approved:
        print("No new events to add.")
        return []

    # 5. Insert
    if commit:
        for ev in approved:
            database.insert_ticker_event(
                ticker=ticker,
                title=ev["title"],
                description=ev["description"],
                event_date=ev.get("event_date"),
                event_date_label=ev.get("event_date_label"),
                sources=[],
                article_ids=ev.get("source_article_ids", []),
                model_name=ev.get("_model"),
            )
        print(f"\nInserted {len(approved)} new events for {ticker}.")
    else:
        print(f"\n[dry run] Would insert {len(approved)} events:")
        for ev in approved:
            print(f"  [{ev.get('event_date_label', '?')}] {ev['title']}")

    return approved


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="NVDA")
    parser.add_argument("--batch", type=int, default=50, help="Max analyses to scan per run")
    parser.add_argument("--commit", action="store_true", help="Write to DB (default is dry run)")
    args = parser.parse_args()

    run_event_pipeline(ticker=args.ticker, batch_size=args.batch, commit=args.commit)
