"""
Event timeline selection: which events from a stream of analysed articles belong on a ticker's
long-term timeline (ticker_events)?

Dataset: a ticker's analyses since a date, oldest first, plus the events it had then, exported
once from production. Variants:

  batch_validate  what production does (event_pipeline): scan each analysis with a cheap
                  model, validate each batch of 50 with a standard model, and add what passes
                  to the list the next batch sees
  monthly_select  the same scan, but against the starting timeline only, then one selection
                  per month that picks at most `cap` events from that month's candidates
  monthly_verify  monthly_select, then a web search per chosen event for whether and when it
                  happened, then a same-event check for each pair of events close in date

An output is one proposed event, keyed by ticker and its earliest source article, so a verdict
carries over to every variant that proposes the event from the same article. An event that
verification moved by more than a week gets the new date in its key: it is a different output
from the misdated one, and a verdict on one says nothing about the other.
"""

import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

import event_pipeline  # NeuralWire code, on sys.path through the evals package
import utils
from evals import REPO_DIR
from evals.llm import complete
from evals.store import Run

DATA_DIR = Path(__file__).parent / "data"
RUBRIC = Path(__file__).parent / "rubric.md"
SCAN_PROMPT_ID = "event_scan_prompt"  # prompts.event_scan_prompt, as production uses it
WORKERS = 8


class FixedModel:
    """Stands in for llms.LLMProviderManager: every call goes to one model, through the eval cache."""

    def __init__(self, model: str, run: Run):
        self.model, self.run = model, run

    def call(
        self,
        prompt,
        tier="standard",
        max_tier=None,
        reasoning=None,
        max_tokens=1000,
        system_prompt=None,
    ):
        c = complete(self.model, prompt, max_tokens=max_tokens, reasoning=reasoning)
        self.run.record_call(c)
        return (c.text, self.model) if c.text else (None, None)


# ── Dataset ──────────────────────────────────────────────────────────────────


def export(ticker: str, since: str, limit: int) -> str:
    """Freeze a ticker's analyses since `since` and its events as a dataset. Read-only on production."""
    import psycopg2
    import psycopg2.extras
    from dotenv import dotenv_values

    url = dotenv_values(REPO_DIR / "python_backend" / ".env")["DATABASE_URL"]
    conn = psycopg2.connect(
        url,
        options="-c default_transaction_read_only=on",
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    c = conn.cursor()
    c.execute(
        """SELECT ar.id, ar.article_id, ar.ticker, ar.impact_headline, ar.ai_summary,
                  a.headline, a.published_at, a.url
             FROM analysis_runs ar JOIN articles a ON ar.article_id = a.id
            WHERE ar.ticker = %s AND ar.run_at >= %s
            ORDER BY a.published_at ASC LIMIT %s""",
        (ticker, since, limit),
    )
    analyses = [dict(r) for r in c.fetchall()]
    c.execute(
        """SELECT title, description, event_date, event_date_label FROM ticker_events
            WHERE ticker = %s ORDER BY event_date DESC""",
        (ticker,),
    )
    existing = [dict(r) for r in c.fetchall()]
    conn.close()

    name = f"{ticker.lower()}_{since}_{len(analyses)}"
    out = DATA_DIR / name
    out.mkdir(parents=True, exist_ok=True)
    (out / "analyses.jsonl").write_text(
        "".join(json.dumps(a, default=str) + "\n" for a in analyses)
    )
    (out / "existing_events.jsonl").write_text(
        "".join(json.dumps(e, default=str) + "\n" for e in existing)
    )
    (out / "meta.json").write_text(
        json.dumps(
            {
                "ticker": ticker,
                "since": since,
                "analyses": len(analyses),
                "existing_events": len(existing),
            }
        )
    )
    return name


def load(dataset: str) -> tuple[dict, list[dict], list[dict]]:
    d = DATA_DIR / dataset
    rows = lambda f: [json.loads(x) for x in (d / f).read_text().splitlines() if x.strip()]  # noqa: E731
    return (
        json.loads((d / "meta.json").read_text()),
        rows("analyses.jsonl"),
        rows("existing_events.jsonl"),
    )


# ── Shared steps ─────────────────────────────────────────────────────────────


def scan_all(analyses: list[dict], existing: list[dict], scan: FixedModel) -> list[dict]:
    """The production scan (event_pipeline._scan_one) over many analyses at once."""
    summary = event_pipeline._format_existing_short(existing)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda a: event_pipeline._scan_one(a, summary, scan), analyses))
    return [candidate for candidate, _answered in results if candidate]


def _source_ids(event: dict) -> list[int]:
    """The event's source article ids as ints (an LLM may return them as strings)."""
    ids = []
    for i in event.get("source_article_ids") or []:
        try:
            ids.append(int(i))
        except (TypeError, ValueError):
            pass
    return ids


def _key(ticker: str, event: dict) -> str:
    ids = _source_ids(event)
    key = f"{ticker}:{min(ids)}" if ids else f"{ticker}:{event.get('title', '')}"
    return f"{key}@{event['event_date']}" if event.get("redated") else key


def _metrics(events: list[dict], articles: dict[int, dict]) -> dict:
    """Events per month, and how many are dated far from their source article (old news as new)."""
    months = Counter(str(e.get("event_date") or "?")[:7] for e in events)
    far = 0
    for e in events:
        src = [articles[i] for i in _source_ids(e) if i in articles]
        try:
            ev = date.fromisoformat(str(e.get("event_date"))[:10])
            pub = min(datetime.fromisoformat(str(a["published_at"])).date() for a in src)
            far += abs((ev - pub).days) > 35
        except (ValueError, TypeError):
            far += 1
    return {
        "events": len(events),
        "max_per_month": max(months.values(), default=0),
        "dated_far_from_source": far,
        "per_month": dict(sorted(months.items())),
    }


def _record_outputs(run: Run, ticker: str, events: list[dict], articles: dict[int, dict]) -> None:
    for e in events:
        src = [articles[i] for i in _source_ids(e) if i in articles]
        run.add_output(
            key=_key(ticker, e),
            title=e.get("title", ""),
            meta=f"{e.get('event_date') or '?'} · {ticker}",
            body=e.get("description", ""),
            context=[
                {"text": a["headline"], "date": str(a["published_at"])[:10], "url": a.get("url")}
                for a in src
            ]
            + ([{"text": f"why: {e['why']}"}] if e.get("why") else [])
            + (
                [{"text": f"date evidence ({e['date_precision']})", "url": e["evidence_url"]}]
                if e.get("evidence_url")
                else []
            ),
            fields={
                "event_date": e.get("event_date"),
                "event_date_label": e.get("event_date_label"),
                "source_article_ids": e.get("source_article_ids"),
                "scan_date": e.get("scan_date"),
            },
        )


# ── Variants ─────────────────────────────────────────────────────────────────


def batch_validate(run: Run, ticker: str, analyses, existing, scan_model, validate_model, batch=50):
    scan, validate = FixedModel(scan_model, run), FixedModel(validate_model, run)
    events, added = list(existing), []
    for i in range(0, len(analyses), batch):
        candidates = scan_all(analyses[i : i + batch], events, scan)
        approved = event_pipeline.run_validate_pass(ticker, candidates, events, validate)
        for e in approved:
            e["event_date"] = e.get("event_date") or "?"  # the scan prompt slices it
        events += approved
        added += approved
    return added


MONTHLY_PROMPT_ID = "events_monthly_v1"  # the default; select_prompt picks another
MONTHLY_PROMPT = """
You are curating the long-term event timeline for {ticker}: the handful of moments an investor
would mark on a multi-year price chart.

Below are the existing timeline and this month's candidate events, which a cheap model flagged
in individual news articles from {month}. Many candidates are minor, and several may describe
the same event.

## Existing timeline
{existing}

## Candidates from {month}
{candidates}

## Choose at most {cap} events
Pick the events that matter most for {ticker} itself. Choose fewer, or none, when the month had
nothing of that weight.

A timeline event is:
- something {ticker} did, or something done to {ticker}, with a material effect on its business:
  quarterly results, an acquisition or divestment, a major product or architecture launch, a
  regulatory, legal or export action, a leadership change, or a deal large enough to change its
  outlook
- dated in {month}, not an older event an article mentioned

Leave out:
- announcements by partners, customers or competitors where {ticker} plays a supporting role
- routine product updates, small partnerships and analyst views
- anything already on the existing timeline, and repeats of the same event among the candidates

## Output
Only a JSON list, most important first, with no other text:
[{{"id": "c3", "title": "Short factual title, at most 10 words", "event_date": "YYYY-MM-DD", "event_date_label": "Human date label", "description": "Exactly one sentence."}}]
Return [] if nothing qualifies.
"""
MONTHLY_PROMPT_V2 = """
You are curating the long-term event timeline for {ticker}: the handful of moments an investor
would mark on a multi-year price chart.

Below are the existing timeline and this month's candidate events, which a cheap model flagged
in individual news articles from {month}. Many candidates are minor, and several may describe
the same event.

## Existing timeline
{existing}

## Candidates from {month}
{candidates}

## Choose at most {cap} events
The cap is a ceiling, not a target. A month with no timeline event is normal: return [] rather
than promote the best of a weak month.

The test: would an investor looking at {ticker}'s five-year price chart want this moment marked?

A timeline event is:
- something {ticker} did, or something done to {ticker}, with a material effect on its business:
  quarterly results, an acquisition or divestment, a major product or architecture launch, a
  regulatory, legal or export action, a leadership change, or a deal large enough to change its
  outlook
- dated in {month}, not an older event an article mentioned

Leave out:
- announcements by partners, customers or competitors where {ticker} plays a supporting role
- routine product updates, small partnerships and analyst views
- market-value milestones and rankings (market cap thresholds, "largest" titles)
- executive appearances, trips, interviews and statements of intent
- talks, reports and rumours that {ticker} has not announced
- supplier and capacity arrangements, unless they change what {ticker} can sell
- anything already on the existing timeline, and repeats of the same event among the candidates

## Output
Only a JSON list, most important first, with no other text:
[{{"id": "c3", "why": "One sentence: what this changed for {ticker}'s business.", "title": "Short factual title, at most 10 words", "event_date": "YYYY-MM-DD", "event_date_label": "Human date label", "description": "Exactly one sentence."}}]
Return [] if nothing qualifies.
"""
MONTHLY_PROMPTS = {MONTHLY_PROMPT_ID: MONTHLY_PROMPT, "events_monthly_v2": MONTHLY_PROMPT_V2}


def _select_month(
    ticker, month, candidates, events, model: FixedModel, cap: int, prompt_id=MONTHLY_PROMPT_ID
) -> list[dict]:
    ids = {f"c{i}": c for i, c in enumerate(candidates, start=1)}
    lines = [
        f"[{cid}] {c.get('event_date') or '?'} | {c['title']} | {c.get('description', '')}"
        for cid, c in ids.items()
    ]
    prompt = MONTHLY_PROMPTS[prompt_id].format(
        ticker=ticker,
        month=month,
        existing=event_pipeline._format_existing_full(events),
        candidates="\n".join(lines),
        cap=cap,
    )
    raw, _ = model.call(prompt, max_tokens=1500, reasoning=False)
    chosen = []
    for row in utils.clean_json_response(raw)[: cap * 2]:
        source = ids.get(str(row.get("id", "")).strip("[] ")) if isinstance(row, dict) else None
        if source is None or len(chosen) == cap:
            continue
        chosen.append(
            {
                "title": row.get("title") or source["title"],
                "event_date": row.get("event_date") or source.get("event_date") or "?",
                "event_date_label": row.get("event_date_label") or source.get("event_date_label"),
                "description": row.get("description") or source.get("description", ""),
                "source_article_ids": source["source_article_ids"],
                "why": row.get("why"),
            }
        )
    return chosen


def monthly_select(
    run: Run,
    ticker: str,
    analyses,
    existing,
    scan_model,
    select_model,
    cap=3,
    select_prompt=MONTHLY_PROMPT_ID,
):
    scan, select = FixedModel(scan_model, run), FixedModel(select_model, run)
    candidates = scan_all(analyses, existing, scan)  # duplicates are left to the monthly step
    published = {a["article_id"]: str(a["published_at"])[:7] for a in analyses}
    by_month = defaultdict(list)
    for c in candidates:
        by_month[published.get(c["source_article_ids"][0], "?")].append(c)
    events, added = list(existing), []
    for month in sorted(by_month):
        chosen = _select_month(ticker, month, by_month[month], events, select, cap, select_prompt)
        events += chosen
        added += chosen
    return added


VERIFY_PROMPT_ID = "events_verify_v2"
VERIFY_PROMPT = """
You are checking one proposed event for {ticker}'s long-term timeline against the web.

Proposed event: {title}
Description: {description}
Found in: "{headline}", published {published}

News articles often mention older events in passing, so the event may have happened long before
the article. Search for the original announcement.

Answer with:
- status: "happened" if {ticker} announced it or it demonstrably took place; "unconfirmed" if it
  is a rumour, a report of plans or talks, something {ticker} only considered, or you cannot
  find it
- event_date: the day it was first announced or took place, not when it was reported later
- precision: "day" if sources give the exact day, "month" if only the month (then event_date is
  the 1st of that month)
- event_date_label: "December 24, 2025", or "December 2025" for month precision
- title: a short factual title, at most 10 words, corrected to match the sources
- description: exactly one sentence, with names and figures as the sources give them
- evidence_url: the source that gives the date

Only JSON, with no other text:
{{"status": "happened", "event_date": "YYYY-MM-DD", "precision": "day", "event_date_label": "...", "title": "...", "description": "...", "evidence_url": "..."}}
"""

SAME_EVENT_PROMPT_ID = "events_same_v2"
SAME_EVENT_PROMPT = """
Do these two entries on {ticker}'s timeline describe the same event?

A: {a}
B: {b}

The same announcement is one event however it is described: one earnings report is one event
whether an entry quotes its quarterly or its full-year figures. Related but distinct events are
different: a product unveiled and the same product entering production later, a deal and a
later expansion of it, results for two different quarters.

Answer with one word: same or different.
"""
REDATED_DAYS = 7
SAME_EVENT_DAYS = 3  # repeats are only looked for among events this close in date


def _parse_object(raw) -> dict | None:
    data = utils.clean_json_response(raw) if raw else None
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    return data if isinstance(data, dict) else None


def _verify_one(ticker, event, articles, model: FixedModel) -> tuple[dict | None, str]:
    """(the event with its verified date and wording, "ok") or (None, why it was dropped)."""
    src = min(
        (articles[i] for i in _source_ids(event) if i in articles),
        key=lambda a: str(a["published_at"]),
        default={"headline": "?", "published_at": "?"},
    )
    prompt = VERIFY_PROMPT.format(
        ticker=ticker,
        title=event["title"],
        description=event.get("description", ""),
        headline=src["headline"],
        published=str(src["published_at"])[:10],
    )
    raw, _ = model.call(prompt, max_tokens=600, reasoning=False)
    data = _parse_object(raw)
    if data is None:
        return None, "unreadable"
    if data.get("status") != "happened":
        return None, "unconfirmed"
    try:
        verified = date.fromisoformat(str(data.get("event_date"))[:10])
    except ValueError:
        return None, "unreadable"
    try:
        moved = abs((verified - date.fromisoformat(str(event["event_date"])[:10])).days)
    except ValueError:
        moved = REDATED_DAYS + 1
    precision = "month" if data.get("precision") == "month" else "day"
    label = data.get("event_date_label") or (
        verified.strftime("%B %Y")
        if precision == "month"
        else f"{verified:%B} {verified.day}, {verified.year}"
    )
    return {
        **event,
        "title": data.get("title") or event["title"],
        "description": data.get("description") or event.get("description", ""),
        "scan_title": event["title"],
        "scan_date": event["event_date"],
        "event_date": verified.isoformat(),
        "event_date_label": label,
        "date_precision": precision,
        "evidence_url": data.get("evidence_url"),
        "redated": moved > REDATED_DAYS,
    }, "ok"


def _event_day(e: dict) -> date | None:
    try:
        return date.fromisoformat(str(e.get("event_date"))[:10])
    except ValueError:
        return None


def _month_only(e: dict) -> bool:
    label = str(e.get("event_date_label") or "")
    return e.get("date_precision") == "month" or (len(label.split()) == 2 and label[-4:].isdigit())


def _could_repeat(a: dict, b: dict) -> bool:
    """Close enough in date (or sharing an evidence page) that they might be the same event."""
    if a.get("evidence_url") and a.get("evidence_url") == b.get("evidence_url"):
        return True
    da, db = _event_day(a), _event_day(b)
    if da is None or db is None:
        return False
    if _month_only(a) or _month_only(b):
        return (da.year, da.month) == (db.year, db.month)
    return abs((da - db).days) <= SAME_EVENT_DAYS


def _line(e: dict) -> str:
    return f"{e.get('event_date_label') or e.get('event_date')} | {e['title']} | {e.get('description', '')}"


def _drop_repeats(ticker, events, existing, model: FixedModel) -> tuple[list[dict], int]:
    """
    The events without repeats, oldest first, and how many were dropped. Only pairs close in
    date are compared, one call per pair, so a model can't merge events weeks apart.
    """
    kept = []
    for e in sorted(events, key=lambda e: e["event_date"]):
        for other in [x for x in existing + kept if _could_repeat(e, x)]:
            prompt = SAME_EVENT_PROMPT.format(ticker=ticker, a=_line(other), b=_line(e))
            raw, _ = model.call(prompt, max_tokens=10, reasoning=False)
            if (raw or "").strip().lower().startswith("same"):
                e["repeats"] = other["title"]
                break
        else:
            kept.append(e)
    return kept, len(events) - len(kept)


def monthly_verify(
    run: Run,
    ticker: str,
    analyses,
    existing,
    scan_model,
    select_model,
    verify_model,
    dedup_model,
    cap=3,
    select_prompt=MONTHLY_PROMPT_ID,
):
    chosen = monthly_select(
        run, ticker, analyses, existing, scan_model, select_model, cap, select_prompt
    )
    articles = {a["article_id"]: a for a in analyses}
    verify = FixedModel(verify_model, run)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda e: _verify_one(ticker, e, articles, verify), chosen))
    confirmed = sorted((e for e, _ in results if e), key=lambda e: e["event_date"])
    why = Counter(reason for _, reason in results)
    kept, repeats = _drop_repeats(ticker, confirmed, existing, FixedModel(dedup_model, run))
    return kept, {
        "selected": len(chosen),
        "dropped_unconfirmed": why["unconfirmed"],
        "dropped_unreadable": why["unreadable"],
        "dropped_repeats": repeats,
        "dropped": [
            f"{e['event_date']} {e['title']} (repeats: {e['repeats']})"
            for e in confirmed
            if "repeats" in e
        ]
        + [
            f"{e['event_date']} {e['title']} ({why})"
            for e, (v, why) in zip(chosen, results, strict=True)
            if v is None
        ],
        "redated": sum(e["redated"] for e in kept),
        "month_precision": sum(e["date_precision"] == "month" for e in kept),
    }


VARIANTS = {
    "batch_validate": {
        "run": batch_validate,
        "models": {"scan_model": "tencent/hy3", "validate_model": "tencent/hy3"},
        "params": {"batch": 50},
        "prompt_id": f"{SCAN_PROMPT_ID} + event_validate_prompt",
    },
    "monthly_select": {
        "run": monthly_select,
        "models": {"scan_model": "tencent/hy3", "select_model": "tencent/hy3"},
        "params": {"cap": 3},
        "prompt_id": f"{SCAN_PROMPT_ID} + {MONTHLY_PROMPT_ID}",
    },
    "monthly_verify": {
        "run": monthly_verify,
        "models": {
            "scan_model": "tencent/hy3",
            "select_model": "tencent/hy3",
            "verify_model": "tencent/hy3:online",
            "dedup_model": "tencent/hy3",
        },
        "params": {"cap": 4},
        "prompt_id": f"{SCAN_PROMPT_ID} + {MONTHLY_PROMPT_ID} + {VERIFY_PROMPT_ID} + {SAME_EVENT_PROMPT_ID}",
    },
}


def run(variant: str, dataset: str, models: dict | None = None, params: dict | None = None) -> Run:
    spec = VARIANTS[variant]
    models = {**spec["models"], **(models or {})}
    params = {**spec["params"], **(params or {})}
    meta, analyses, existing = load(dataset)
    prompt_id = spec["prompt_id"].replace(
        MONTHLY_PROMPT_ID, params.get("select_prompt", MONTHLY_PROMPT_ID)
    )
    r = Run("events", variant, dataset, models, prompt_id, params)
    added = spec["run"](r, meta["ticker"], analyses, existing, **models, **params)
    added, extra = added if isinstance(added, tuple) else (added, {})
    articles = {a["article_id"]: a for a in analyses}
    _record_outputs(r, meta["ticker"], added, articles)
    r.save({**_metrics(added, articles), **extra})
    return r
