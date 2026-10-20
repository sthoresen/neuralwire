"""
Event scan: the first, cheap call of the event pipeline, which reads one analysis at a time and
flags the ones that might describe a timeline event. Everything it flags goes on to selection,
so it should flag as little as possible while still catching every approved event.

Scored against approved.json, a fixed answer key: the events judged keep on the events problem's
review page, each with every analysis in the dataset that reports it. An approved event counts
as found when the scan flags at least one of its analyses. No verdicts are needed per run.

Uses the events problem's datasets. Runs differ in the model, the prompt (param `prompt`), the
reasoning effort (param `reasoning`: "off" or an effort such as "low"), and whether the scan
remembers what it flagged recently (params `memory_days` and `chunk`). The `jev` variant asks
Jev instead, which scores each analysis rather than writing a reply.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import event_pipeline
import prompts
import utils
from evals import jev
from evals.problems import events
from evals.store import Run

APPROVED = Path(__file__).parent / "approved.json"

SCAN_V2 = """
You are the first filter for {ticker}'s long-term event timeline: the handful of moments an
investor would mark on a five-year price chart. Most news articles report no such event. Flag
this one only if it does.

## Existing timeline (do not flag these again)
{existing_events_summary}

## Article
Published:      {published_at}
Headline:       {headline}
Impact:         {impact_headline}
Summary:        {ai_summary}

## Flag it if the article reports one of these, done by {ticker} or done to {ticker}:
- quarterly or annual results
- an acquisition, merger, divestment or major investment in another company
- the launch or unveiling of a major product, platform or architecture
- a regulatory, legal or export-control action
- a change of CEO or other top leadership
- a major financing or capital return: a large bond or equity raise, a buyback, a stock split
- a deal large enough to change {ticker}'s outlook

## Do not flag
- announcements by partners, customers or suppliers that use, build on or sell {ticker}'s products
- small partnerships, collaborations, integrations and customer deployments
- analyst views, price targets, earnings previews, valuation pieces and fund holdings
- share-price moves, market-value milestones and rankings
- executive appearances, interviews, trips and statements of intent
- talks, reports and rumours that {ticker} has not announced
- events already on the existing timeline

A later step chooses among the flagged articles, so when an article does report one of the
events above, flag it even if you are unsure how big it is.

Respond with JSON only, no other text:
{{"is_event": false}}
OR
{{"is_event": true, "title": "Short factual title (max 10 words)", "event_date": "YYYY-MM-DD", "event_date_label": "Human date label", "description": "Exactly one sentence."}}
"""

# v2 plus memory of recent candidates, and releases for developers only when widely covered
SCAN_V3 = SCAN_V2.replace(
    """{existing_events_summary}
""",
    """{existing_events_summary}

## Already flagged in the last {memory_days} days (do not flag these again)
{recent_candidates}
""",
).replace(
    """- talks, reports and rumours""",
    """- releases for developers and researchers (models, SDKs, toolkits, frameworks), unless they are
  big enough for sustained coverage in mainstream financial news
- talks, reports and rumours""",
)
assert SCAN_V3.count("{recent_candidates}") == 1 and "SDKs" in SCAN_V3

SCAN_PROMPTS = {
    "event_scan_prompt": prompts.event_scan_prompt,
    "scan_v2": SCAN_V2,
    "scan_v3": SCAN_V3,
}


def _flag(
    analysis: dict,
    existing_summary: str,
    template: str,
    model,
    recent="(none)",
    memory_days=0,
    reasoning="off",
) -> dict | None:
    """The candidate if the model flags the analysis, else None (also for unreadable replies)."""
    prompt = template.format(
        ticker=analysis["ticker"],
        existing_events_summary=existing_summary,
        recent_candidates=recent,
        memory_days=memory_days,
        published_at=analysis.get("published_at", "unknown"),
        headline=analysis.get("headline", ""),
        impact_headline=analysis.get("impact_headline", ""),
        ai_summary=analysis.get("ai_summary", ""),
    )
    # reasoning tokens count against max_tokens, so a reasoning model needs room for both
    effort = False if reasoning == "off" else reasoning
    raw, _ = model.call(prompt, max_tokens=500 if reasoning == "off" else 4000, reasoning=effort)
    data = utils.clean_json_response(raw) if raw else None
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    if not isinstance(data, dict) or not data.get("is_event"):
        return None
    return {**data, "article_id": analysis["article_id"]}


def score(flagged: set[int], approved: dict) -> dict:
    """Which approved events the flagged analyses cover, per judge, and what was missed."""
    out = {}
    for judge in ("you", "claude"):
        evs = {k: v for k, v in approved.items() if not k.startswith("_") and v["by"] == judge}
        found = [k for k, v in evs.items() if flagged & set(v["articles"])]
        out[f"found_{judge}"] = f"{len(found)}/{len(evs)}"
        out[f"missed_{judge}"] = [evs[k]["title"] for k in evs if k not in found]
    reporting = {a for k, v in approved.items() if not k.startswith("_") for a in v["articles"]}
    out["reporting_flagged"] = f"{len(flagged & reporting)}/{len(reporting)}"
    return out


def _published(a: dict) -> date:
    return datetime.fromisoformat(str(a["published_at"])).date()


def _scan_in_order(analyses, flag, memory_days: int, chunk: int) -> list[dict]:
    """
    flag(analysis, recent) -> candidate or None, where recent lists what was flagged lately.
    Without memory every analysis is scanned independently. With memory_days, analyses go in
    publication order, `chunk` at a time (in parallel within a chunk, like one production run),
    and each chunk sees what earlier chunks flagged in the last memory_days days.
    """
    with ThreadPoolExecutor(max_workers=events.WORKERS) as pool:
        if not memory_days:
            return [c for c in pool.map(lambda a: flag(a, "(none)"), analyses) if c]
        ordered = sorted(analyses, key=lambda a: str(a["published_at"]))
        published = {a["article_id"]: _published(a) for a in ordered}
        flagged = []
        for i in range(0, len(ordered), chunk):
            batch = ordered[i : i + chunk]
            since = _published(batch[0]) - timedelta(days=memory_days)
            recent = [c for c in flagged if published[c["article_id"]] >= since]
            lines = (
                "\n".join(f"{published[c['article_id']]} | {c.get('title', '')}" for c in recent)
                or "(none)"
            )
            flagged += [c for c in pool.map(lambda a, lines=lines: flag(a, lines), batch) if c]
        return flagged


def scan(
    run: Run,
    ticker,
    analyses,
    existing,
    scan_model,
    prompt="event_scan_prompt",
    memory_days=0,
    chunk=10,
    reasoning="off",
):
    """An LLM reads each analysis and answers is_event, with a title for what it flags."""
    model = events.FixedModel(scan_model, run)
    summary = event_pipeline._format_existing_short(existing)
    template = SCAN_PROMPTS[prompt]

    def flag(a, recent):
        return _flag(a, summary, template, model, recent, memory_days, reasoning)

    return _scan_in_order(analyses, flag, memory_days, chunk)


# ── Jev ──────────────────────────────────────────────────────────────────────

TIMELINE = (
    "NeuralWire keeps a long-term event timeline for each company it covers: the handful of "
    "moments an investor would mark on a five-year price chart."
)


def jev_questions(ticker: str) -> dict:
    """Two framings of the scan question, asked in the same call (the text is billed once)."""
    ask = f"{TIMELINE} Does this article report a new event for {ticker}'s timeline?"
    return {
        "plain": {"type": "noul", "instructions": ask},
        "criteria": {
            "type": "noul",
            "instructions": ask,
            "criteria": {
                "true": f"The article reports something done by or to {ticker}: quarterly or annual "
                "results; an acquisition, merger, divestment or major investment in another company; "
                "the launch or unveiling of a major product, platform or architecture; a regulatory, "
                "legal or export-control action; a change of CEO or other top leadership; a major "
                "financing or capital return (a large bond or equity raise, a buyback, a stock "
                "split); or a deal large enough to change its outlook",
                "false": f"An announcement by a partner, customer or supplier that uses or sells "
                f"{ticker}'s products; a small partnership, collaboration or customer deployment; "
                "analyst views, previews, valuation pieces or fund holdings; share-price moves, "
                "market-value milestones or rankings; executive appearances, interviews, trips or "
                "statements of intent; a release for developers or researchers without sustained "
                f"mainstream coverage; talks, reports or rumours {ticker} has not announced; an "
                "event already on the timeline or already flagged recently",
            },
        },
    }


def _jev_state(a: dict, existing_summary: str, recent: str, memory_days: int) -> str:
    lines = [f"Company: {a['ticker']}", "", "Existing timeline:", existing_summary, ""]
    if memory_days:
        lines += [f"Already flagged in the last {memory_days} days:", recent, ""]
    lines += [
        f"Article published: {a.get('published_at')}",
        f"Headline: {a.get('headline', '')}",
        f"Impact: {a.get('impact_headline', '')}",
        f"Summary: {a.get('ai_summary', '')}",
    ]
    return "\n".join(lines)


SWEEP = (0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


def jev_scan(
    run: Run,
    ticker,
    analyses,
    existing,
    question="criteria",
    threshold=0.5,
    memory_days=0,
    chunk=10,
):
    """
    Jev scores each analysis; it is flagged when the chosen question's probability reaches
    `threshold`. Jev writes no text, so a flag's title is the analysis's own impact headline.
    Without memory the scores don't depend on the threshold, so the run also reports what every
    threshold in SWEEP would flag, for both questions.
    """
    summary = event_pipeline._format_existing_short(existing)
    questions = jev_questions(ticker)
    scores = {}

    def flag(a, recent):
        got, call = jev.ask(_jev_state(a, summary, recent, memory_days), questions)
        run.record_call(call)
        if not got:
            return None
        scores[a["article_id"]] = got
        if got[question] < threshold:
            return None
        return {
            "article_id": a["article_id"],
            "title": a.get("impact_headline", ""),
            "score": got[question],
        }

    flagged = _scan_in_order(analyses, flag, memory_days, chunk)
    if memory_days:
        return flagged, {}
    approved = json.loads(APPROVED.read_text()).get(run.meta["dataset"], {})
    sweep = {}
    for q in questions:
        for t in SWEEP:
            ids = {aid for aid, sc in scores.items() if sc[q] >= t}
            sweep[f"{q}@{t}"] = f"{len(ids)} flagged, {score(ids, approved)['found_you']}"
    return flagged, {"sweep": sweep}


VARIANTS = {
    "scan": {
        "run": scan,
        "models": {"scan_model": "tencent/hy3"},
        "params": {
            "prompt": "event_scan_prompt",
            "memory_days": 0,
            "chunk": 10,
            "reasoning": "off",
        },
    },
    "jev": {
        "run": jev_scan,
        "models": {},
        "params": {"question": "criteria", "threshold": 0.5, "memory_days": 0, "chunk": 10},
    },
}


def export(ticker: str, since: str, limit: int) -> str:
    return events.export(ticker, since, limit)


def run(variant: str, dataset: str, models: dict | None = None, params: dict | None = None) -> Run:
    spec = VARIANTS[variant]
    models = {**spec["models"], **(models or {})}
    params = {**spec["params"], **(params or {})}
    meta, analyses, existing = events.load(dataset)
    prompt_id = params.get("prompt") or f"jev_{params['question']}_v1"
    r = Run("event_scan", variant, dataset, {**models} or {"model": jev.MODEL}, prompt_id, params)
    flagged = spec["run"](r, meta["ticker"], analyses, existing, **models, **params)
    flagged, extra = flagged if isinstance(flagged, tuple) else (flagged, {})
    articles = {a["article_id"]: a for a in analyses}
    for c in flagged:
        a = articles[c["article_id"]]
        r.add_output(
            key=f"{meta['ticker']}:a{c['article_id']}",
            title=c.get("title", ""),
            meta=f"{str(a['published_at'])[:10]} · {meta['ticker']}",
            body=c.get("description", ""),
            context=[
                {"text": a["headline"], "date": str(a["published_at"])[:10], "url": a.get("url")}
            ],
        )
    approved = json.loads(APPROVED.read_text()).get(dataset, {})
    ids = {c["article_id"] for c in flagged}
    r.save({"analyses": len(analyses), "flagged": len(flagged), **score(ids, approved), **extra})
    return r
