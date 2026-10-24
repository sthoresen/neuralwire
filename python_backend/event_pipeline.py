"""
event_pipeline.py

Builds each ticker's long-term event timeline (ticker_events) from its analysed articles. Each
run has two phases:

Scan: up to `batch_size` unchecked analyses, oldest published first, one at a time. A cheap
model flags the ones that report a possible timeline event. It sees the timeline and what was
flagged within SCAN_MEMORY_DAYS of the article, so the same news isn't flagged once per article.
Flags are stored as candidates (event_candidates).

Select: for every month that is over and has no unchecked analyses left, a standard model ranks
the month's best candidates. In that order, each pick is checked on the web for whether and when
it happened, then compared with timeline events close to its date, until MONTHLY_CAP picks have
passed; one that fails leaves its slot to the next, so the ranking includes SPARE_PICKS spares.
Only recent events are taken: a month more than MAX_MONTHS_BACK back is not selected, and an
event that verification dates more than MAX_DAYS_BEFORE_ARTICLE before its article (an old event
the article mentioned) is dropped. Candidates that arrive for a month already selected (an old
article analysed late) are selected the same way, within what is left of that month's cap. If
any call in a month gets no reply, the month stays pending for the next run.

The prompts and these settings are measured in evals/ (problems events and event_scan). Storage
goes through a store, DBStore here and MemoryStore in evals and tests, so the evals run this
exact code.
"""

import os
import sys
from collections import Counter
from datetime import UTC, date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import database
import prompts
import utils

SCAN_MEMORY_DAYS = 30
MONTHLY_CAP = 4
VERIFY_MODEL = "tencent/hy3:online"  # Hy3 with OpenRouter's web search
REDATED_DAYS = 7  # a verified date this far from the scan's counts as a correction
SAME_EVENT_DAYS = 3  # repeats are only looked for among events this close in date
# The timeline only takes recent events: months further back than this are never selected (old
# articles get analysed too), and an event dated this long before its article is dropped.
MAX_MONTHS_BACK = 12
MAX_DAYS_BEFORE_ARTICLE = 31
SPARE_PICKS = 3  # extra ranked picks, to fill the slot of a pick that fails verification

# ── Formatting ───────────────────────────────────────────────────────────────


def _format_existing_short(events):
    """One line per timeline event (month and title), for the scan prompt."""
    if not events:
        return "(none yet)"
    return "\n".join(f"{str(e.get('event_date') or '?')[:7]} | {e['title']}" for e in events)


def _format_existing_full(events):
    """One line per timeline event with its description, for the selection prompt."""
    if not events:
        return "(none yet)"
    return "\n".join(
        f"[{e.get('event_date_label') or e.get('event_date', '?')}] {e['title']} — {e['description']}"
        for e in events
    )


def _parse_object(raw) -> dict | None:
    """The JSON object in a reply. clean_json_response wraps a lone {...} reply in a list."""
    data = utils.clean_json_response(raw) if raw else None
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    return data if isinstance(data, dict) else None


def _day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _published(row: dict) -> date | None:
    """The article's publish date; published_at is a datetime from the database, a string in evals."""
    value = row.get("published_at")
    if isinstance(value, datetime):
        return value.date()
    return _day(value)


def _month(row: dict) -> str:
    day = _published(row)
    return f"{day:%Y-%m}" if day else "unknown"


# ── Stores ───────────────────────────────────────────────────────────────────


class DBStore:
    """The pipeline's storage for one ticker, in production's database."""

    def __init__(self, ticker: str):
        self.ticker = ticker

    def unchecked(self, limit):
        return database.get_unchecked_analyses(self.ticker, limit=limit)

    def mark_checked(self, analysis_ids):
        database.mark_analyses_event_checked(analysis_ids)

    def timeline(self):
        return database.get_ticker_events(self.ticker)

    def recent_candidates(self, start: date, end: date):
        return database.get_event_candidates_between(self.ticker, start, end)

    def add_candidate(self, candidate):
        database.insert_event_candidate(self.ticker, candidate)

    def months_ready(self, today: date):
        return database.get_event_months_ready(self.ticker, f"{today:%Y-%m}")

    def pending_candidates(self, month):
        return database.get_pending_event_candidates(self.ticker, month)

    def selected_count(self, month):
        return database.count_selected_event_candidates(self.ticker, month)

    def add_event(self, event) -> int | None:
        return database.insert_ticker_event(
            ticker=self.ticker,
            title=event["title"],
            description=event["description"],
            event_date=event["event_date"],
            event_date_label=event["event_date_label"],
            sources=[event["evidence_url"]] if event.get("evidence_url") else [],
            article_ids=[event["article_id"]],
            model_name=event.get("model_name"),
        )

    def decide(self, candidate_ids, status, event_id=None):
        database.decide_event_candidates(candidate_ids, status, event_id)


class MemoryStore:
    """The same interface over lists, for evals and tests. Nothing is written anywhere."""

    def __init__(self, ticker: str, analyses: list[dict], timeline: list[dict]):
        self.ticker = ticker
        self.analyses = sorted(
            ({**a, "checked": False} for a in analyses), key=lambda a: str(a["published_at"])
        )
        self.events = [dict(e) for e in timeline]
        self.candidates: list[dict] = []

    def unchecked(self, limit):
        return [a for a in self.analyses if not a["checked"]][:limit]

    def mark_checked(self, analysis_ids):
        ids = set(analysis_ids)
        for a in self.analyses:
            if a["id"] in ids:
                a["checked"] = True

    def timeline(self):
        return sorted(self.events, key=lambda e: str(e.get("event_date") or ""), reverse=True)

    def recent_candidates(self, start, end):
        return [c for c in self.candidates if _published(c) and start <= _published(c) <= end]

    def add_candidate(self, candidate):
        self.candidates.append({**candidate, "id": len(self.candidates) + 1, "status": "pending"})

    def months_ready(self, today):
        unchecked = {_month(a) for a in self.analyses if not a["checked"]}
        pending = {c["month"] for c in self.candidates if c["status"] == "pending"}
        return sorted(m for m in pending if m < f"{today:%Y-%m}" and m not in unchecked)

    def pending_candidates(self, month):
        return [c for c in self.candidates if c["month"] == month and c["status"] == "pending"]

    def selected_count(self, month):
        return sum(c["month"] == month and c["status"] == "selected" for c in self.candidates)

    def add_event(self, event):
        self.events.append(event)
        return len(self.events)

    def decide(self, candidate_ids, status, event_id=None):
        ids = set(candidate_ids)
        for c in self.candidates:
            if c["id"] in ids:
                c["status"], c["event_id"] = status, event_id


# ── Scan ─────────────────────────────────────────────────────────────────────


def scan_one(ar, timeline_summary, recent, llm):
    """
    Asks the cheap model whether one analysis reports a possible timeline event.
    Returns (candidate or None, answered). answered is False when no model replied, so the
    analysis stays unchecked for the next run.
    """
    prompt = prompts.event_scan_prompt.format(
        ticker=ar["ticker"],
        existing_events_summary=timeline_summary,
        recent_candidates=recent,
        memory_days=SCAN_MEMORY_DAYS,
        published_at=ar.get("published_at", "unknown"),
        headline=ar.get("headline", ""),
        impact_headline=ar.get("impact_headline", ""),
        ai_summary=ar.get("ai_summary", ""),
    )
    raw, model_used = llm.call(prompt, tier="economy", reasoning=False, max_tokens=500)
    if not raw:
        return None, False
    data = _parse_object(raw)
    if data is None:
        print(f"    [scan] unreadable reply for analysis_run {ar['id']}: {raw[:80]!r}")
        return None, True
    if not data.get("is_event"):
        return None, True
    return {
        "analysis_run_id": ar["id"],
        "article_id": ar["article_id"],
        "published_at": ar.get("published_at"),
        "month": _month(ar),
        "headline": ar.get("headline", ""),
        "title": (data.get("title") or "").strip(),
        "description": (data.get("description") or "").strip(),
        "event_date": data.get("event_date"),
        "event_date_label": data.get("event_date_label"),
        "model_name": model_used,
    }, True


def run_scan(store, llm, batch_size=50) -> Counter:
    stats = Counter()
    analyses = store.unchecked(batch_size)
    summary = _format_existing_short(store.timeline())
    for ar in analyses:
        day = _published(ar) or date.today()
        window = timedelta(days=SCAN_MEMORY_DAYS)
        recent = store.recent_candidates(day - window, day + window)
        lines = "\n".join(f"{_published(c)} | {c['title']}" for c in recent) or "(none)"
        candidate, answered = scan_one(ar, summary, lines, llm)
        if not answered:
            stats["no_reply"] += 1
            continue
        if candidate:
            store.add_candidate(candidate)
            stats["flagged"] += 1
        store.mark_checked([ar["id"]])  # one at a time, so a crash keeps what was done
        stats["scanned"] += 1
    return stats


# ── Select ───────────────────────────────────────────────────────────────────


def select_month(ticker, month, candidates, timeline, cap, llm):
    """
    The standard model's picks among a month's candidates, best first: a list of
    (candidate, the pick's wording), or None if no model replied.
    """
    ids = {f"c{i}": c for i, c in enumerate(candidates, start=1)}
    lines = [
        f"[{cid}] {c.get('event_date') or '?'} | {c['title']} | {c.get('description', '')}"
        for cid, c in ids.items()
    ]
    prompt = prompts.event_select_prompt.format(
        ticker=ticker,
        month=month,
        existing=_format_existing_full(timeline),
        candidates="\n".join(lines),
        cap=cap,
    )
    raw, model_used = llm.call(prompt, tier="standard", reasoning=False, max_tokens=1500)
    if not raw:
        return None
    picks = []
    for row in utils.clean_json_response(raw):
        source = ids.get(str(row.get("id", "")).strip("[] ")) if isinstance(row, dict) else None
        if source is None or any(source is c for c, _ in picks):
            continue
        picks.append((source, {**row, "model_name": model_used}))
        if len(picks) == cap:  # the prompt asks for at most cap; the code makes sure
            break
    return picks


def verify(ticker, candidate, pick, llm):
    """
    Checks a pick on the web. Returns (event, "ok"), or (None, why): "unconfirmed" if it is a
    rumour or can't be found, "too_old" if it happened long before the article, "unreadable"
    for a reply that can't be parsed, "no_reply" if the call failed (the month is then retried).
    """
    title = pick.get("title") or candidate["title"]
    prompt = prompts.event_verify_prompt.format(
        ticker=ticker,
        title=title,
        description=pick.get("description") or candidate.get("description", ""),
        headline=candidate.get("headline", ""),
        published=str(candidate.get("published_at"))[:10],
    )
    try:
        raw, _ = llm.call_model(VERIFY_MODEL, prompt, reasoning=False, max_tokens=600)
    except RuntimeError:
        return None, "no_reply"
    data = _parse_object(raw)
    if data is None:
        return None, "unreadable"
    if data.get("status") != "happened":
        return None, "unconfirmed"
    verified = _day(data.get("event_date"))
    if verified is None:
        return None, "unreadable"
    published = _published(candidate)
    if published and verified < published - timedelta(days=MAX_DAYS_BEFORE_ARTICLE):
        return None, "too_old"
    month_only = data.get("precision") == "month"
    label = data.get("event_date_label") or (
        f"{verified:%B %Y}" if month_only else f"{verified:%B} {verified.day}, {verified.year}"
    )
    return {
        "candidate_id": candidate["id"],
        "article_id": candidate["article_id"],
        "title": data.get("title") or title,
        "description": data.get("description") or pick.get("description") or "",
        "event_date": verified.isoformat(),
        "event_date_label": label,
        "date_precision": "month" if month_only else "day",
        "evidence_url": data.get("evidence_url"),
        "model_name": pick.get("model_name"),
        "why": pick.get("why"),  # the selection's reason, shown in reviews; not stored
        "scan_date": candidate.get("event_date"),
    }, "ok"


def _month_only(e: dict) -> bool:
    label = str(e.get("event_date_label") or "")
    return e.get("date_precision") == "month" or (len(label.split()) == 2 and label[-4:].isdigit())


def could_repeat(a: dict, b: dict) -> bool:
    """Close enough in date, or sharing an evidence page, that they might be the same event."""
    if a.get("evidence_url") and a.get("evidence_url") == b.get("evidence_url"):
        return True
    da, db = _day(a.get("event_date")), _day(b.get("event_date"))
    if da is None or db is None:
        return False
    if _month_only(a) or _month_only(b):
        return (da.year, da.month) == (db.year, db.month)
    return abs((da - db).days) <= SAME_EVENT_DAYS


def _line(e: dict) -> str:
    return f"{e.get('event_date_label') or e.get('event_date')} | {e['title']} | {e.get('description', '')}"


def repeat_of(ticker, event, others, llm):
    """
    The title of the event in `others` that `event` repeats, "" if none, or None if a check got
    no reply. Only events close in date are compared, one call per pair, so a model can't merge
    events weeks apart.
    """
    for other in [x for x in others if could_repeat(event, x)]:
        prompt = prompts.event_same_prompt.format(ticker=ticker, a=_line(other), b=_line(event))
        raw, _ = llm.call(prompt, tier="economy", reasoning=False, max_tokens=10)
        if not raw:
            return None
        if raw.strip().lower().startswith("same"):
            return other["title"]
    return ""


def drop_repeats(ticker, events, timeline, llm):
    """(kept, repeats) with each repeat paired with the title it repeats, or None on no reply."""
    kept, repeats = [], []
    for e in sorted(events, key=lambda e: e["event_date"]):
        of = repeat_of(ticker, e, timeline + kept, llm)
        if of is None:
            return None
        (repeats.append((e, of)) if of else kept.append(e))
    return kept, repeats


def run_select(store, llm, today: date, cap=MONTHLY_CAP) -> Counter:
    """
    For each ready month: the selection ranks up to `room + SPARE_PICKS` candidates, and they
    are verified and checked for repeats in that order until the month's room is filled. A pick
    that fails (too old, unconfirmed, a repeat) leaves its slot to the next one in line.
    """
    stats = Counter()
    oldest = f"{today - timedelta(days=round(MAX_MONTHS_BACK * 30.44)):%Y-%m}"
    for month in store.months_ready(today):
        candidates = store.pending_candidates(month)
        if month < oldest:  # an old article; its month is past the timeline's reach
            store.decide([c["id"] for c in candidates], "too_old")
            stats["months_too_old"] += 1
            continue
        room = cap - store.selected_count(month)
        if room <= 0:  # late arrivals for a month that is already full
            store.decide([c["id"] for c in candidates], "not_selected")
            continue
        timeline = store.timeline()
        picks = select_month(store.ticker, month, candidates, timeline, room + SPARE_PICKS, llm)
        if picks is None:
            stats["months_retried"] += 1
            continue

        kept, dropped, failed = [], [], False  # dropped: (candidate, title, status, why)
        for c, pick in picks:
            if len(kept) == room:
                break
            event, why = verify(store.ticker, c, pick, llm)
            if why == "no_reply":
                failed = True
                break
            if event is None:
                status = "too_old" if why == "too_old" else "unconfirmed"
                dropped.append((c, pick.get("title") or c["title"], status, why))
                continue
            of = repeat_of(store.ticker, event, timeline + kept, llm)
            if of is None:
                failed = True
                break
            if of:
                dropped.append((c, event["title"], "repeat", f"repeats: {of}"))
            else:
                kept.append(event)
        if failed:  # nothing is written; the whole month is tried again next run
            stats["months_retried"] += 1
            continue

        for e in kept:
            event_id = store.add_event(e)
            store.decide([e["candidate_id"]], "selected", event_id)
            print(f"    + {e['event_date']} {e['title']}")
        for c, title, status, why in dropped:
            store.decide([c["id"]], status)
            print(f"    - {title} ({why})")
        decided = {e["candidate_id"] for e in kept} | {c["id"] for c, *_ in dropped}
        store.decide([c["id"] for c in candidates if c["id"] not in decided], "not_selected")
        statuses = Counter(status for _, _, status, _ in dropped)
        stats.update(
            months=1,
            events_added=len(kept),
            unconfirmed=statuses["unconfirmed"],
            too_old=statuses["too_old"],
            repeats=statuses["repeat"],
        )
    return stats


# ── Run ──────────────────────────────────────────────────────────────────────


def run_event_pipeline(ticker, batch_size=50, store=None, llm=None, today=None) -> Counter:
    """One run for one ticker: scan, then select every month that is ready. Returns counts."""
    if llm is None:
        import llms

        llm = llms.LLMProviderManager()
    store = store or DBStore(ticker)
    stats = run_scan(store, llm, batch_size)
    stats.update(run_select(store, llm, today or datetime.now(UTC).date()))
    return stats
