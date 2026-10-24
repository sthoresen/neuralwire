"""
Event timeline selection: which events from a stream of analysed articles belong on a ticker's
long-term timeline (ticker_events)?

Dataset: a ticker's analyses since a date, oldest first, plus the events it had then, exported
once from production. Variant:

  pipeline  production's event_pipeline itself, on an in-memory store: run after run of 50
            analyses until the dataset is scanned, selecting each month once it is complete,
            as the scheduled job would. Nothing is written to the database.

Earlier runs in runs/events/ came from variants that designed this pipeline and were retired
when it moved into production (batch_validate, the old production pipeline; monthly_select;
monthly_verify). Their code is in git history at commit 5ae7133.

An output is one proposed event, keyed by ticker and its earliest source article, so a verdict
carries over to every variant that proposes the event from the same article. An event that
verification moved by more than a week gets the new date in its key: it is a different output
from the misdated one, and a verdict on one says nothing about the other.
"""

import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import event_pipeline  # NeuralWire code, on sys.path through the evals package
import prompts
from evals import REPO_DIR
from evals.llm import complete
from evals.store import Run

DATA_DIR = Path(__file__).parent / "data"
RUBRIC = Path(__file__).parent / "rubric.md"
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


# ── Outputs ──────────────────────────────────────────────────────────────────


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


# ── Variant ──────────────────────────────────────────────────────────────────


class PipelineModels:
    """
    Stands in for llms.LLMProviderManager in event_pipeline: the economy and standard tiers each
    go to one model, and the pipeline's verify model can be swapped, all through the eval cache.
    """

    def __init__(self, run: Run, economy_model, standard_model, verify_model):
        self.run = run
        self.tiers = {"economy": economy_model, "standard": standard_model}
        self.verify_model = verify_model

    def _complete(self, model, prompt, reasoning, max_tokens):
        c = complete(model, prompt, max_tokens=max_tokens, reasoning=reasoning)
        self.run.record_call(c)
        return c

    def call(self, prompt, tier="standard", max_tier=None, reasoning=None, max_tokens=1000, **_):
        c = self._complete(self.tiers[tier], prompt, reasoning, max_tokens)
        return (c.text, c.model) if c.text else (None, None)

    def call_model(self, model_id, prompt, reasoning=None, max_tokens=1000, **_):
        model = self.verify_model if model_id == event_pipeline.VERIFY_MODEL else model_id
        c = self._complete(model, prompt, reasoning, max_tokens)
        if not c.text:
            raise RuntimeError(f"call_model: '{model}' failed to produce a response.")
        return c.text, c.model


def pipeline(
    run: Run, ticker, analyses, existing, economy_model, standard_model, verify_model, batch=50
):
    store = event_pipeline.MemoryStore(ticker, analyses, existing)
    llm = PipelineModels(run, economy_model, standard_model, verify_model)
    total = Counter()
    while store.unchecked(1):
        stats = event_pipeline.run_event_pipeline(ticker, batch, store=store, llm=llm)
        total.update(stats)
        if stats["no_reply"] and not stats["scanned"]:
            break  # no model is answering; the counts show it
    added = store.events[len(existing) :]
    for e in added:
        e["source_article_ids"] = [e["article_id"]]
        scanned, verified = _day(e.get("scan_date")), _day(e["event_date"])
        e["redated"] = not scanned or abs((verified - scanned).days) > event_pipeline.REDATED_DAYS
    statuses = Counter(c["status"] for c in store.candidates)
    return added, {
        **total,
        "candidates": dict(statuses),
        "unchecked_left": len(store.unchecked(10**6)),
    }


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


VARIANTS = {
    "pipeline": {
        "run": pipeline,
        "models": {
            "economy_model": "tencent/hy3",
            "standard_model": "tencent/hy3",
            "verify_model": event_pipeline.VERIFY_MODEL,
        },
        "params": {"batch": 50},
        "prompt_id": " + ".join(
            [
                prompts.EVENT_SCAN_PROMPT_ID,
                prompts.EVENT_SELECT_PROMPT_ID,
                prompts.EVENT_VERIFY_PROMPT_ID,
                prompts.EVENT_SAME_PROMPT_ID,
            ]
        ),
    },
}


def run(variant: str, dataset: str, models: dict | None = None, params: dict | None = None) -> Run:
    spec = VARIANTS[variant]
    models = {**spec["models"], **(models or {})}
    params = {**spec["params"], **(params or {})}
    meta, analyses, existing = load(dataset)
    r = Run("events", variant, dataset, models, spec["prompt_id"], params)
    added, extra = spec["run"](r, meta["ticker"], analyses, existing, **models, **params)
    articles = {a["article_id"]: a for a in analyses}
    _record_outputs(r, meta["ticker"], added, articles)
    r.save({**_metrics(added, articles), **extra})
    return r
