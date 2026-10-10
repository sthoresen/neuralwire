"""
x_jev.py

Scores X posts with Jev (typesafe/jev-1.13), a classifier reached through
OpenRouter's System One endpoint. Each post is one call; every question in a
question set gets a probability from 0 to 1, and the post's text is billed once
however many questions there are. How the scores become one rank is code
(rank_score), not part of the questions.

Question sets are versioned in QSETS and never edited once used: labels are
stored per version, so versions can be compared on the same posts and no post
is paid for twice. A new idea is a new version. QSET picks the one in use.
Each labelling batch is archived like the fetches, so --rebuild restores it.
"""

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import requests

import x_sources

JEV_URL = "https://openrouter.ai/api/v1/systemone"
JEV_MODEL = "typesafe/jev-1.13"  # pinned, so scores are reproducible
JEV_PRICE_PER_M = 0.042  # input tokens; output is free. Used when usage carries no cost.
WORKERS = 8

# Narrow signals: v1 ranks with them; v2 keeps them alongside its worth questions.
SIGNALS = {
    "news": {
        "type": "noul",
        "instructions": "The post reports a concrete new development",
        "criteria": {
            "true": "A release, launch, paper, benchmark, dataset, deal, funding round, earnings or "
            "economic data, a policy or central-bank decision, or another specific event, stated as news",
            "false": "Commentary, opinion, a reaction, a joke, a prediction, advice, or old news "
            "without a new fact",
        },
    },
    "substance": {
        "type": "noul",
        "instructions": "The post makes a specific, reasoned point",
        "criteria": {
            "true": "An argument with evidence, numbers, a mechanism or a clear claim someone could "
            "disagree with",
            "false": "A reaction, hype, a vague take, a joke, a teaser, or a bare link",
        },
    },
    "promo": {
        "type": "noul",
        "instructions": "The post promotes something rather than informing",
        "criteria": {
            "true": "Self-promotion, an ad, an event, podcast or livestream plug, a newsletter or "
            "course, a giveaway, or engagement bait",
            "false": "Information or an argument that stands on its own",
        },
    },
    "ai": {
        "type": "noul",
        "instructions": "The post is about artificial intelligence",
        "criteria": {
            "true": "AI models, research, products or companies, chips and compute for AI, AI policy, "
            "or the AI industry",
            "false": "Other technology, markets or topics with no AI angle",
        },
    },
    "macro": {
        "type": "noul",
        "instructions": "The post is about the economy or markets as a whole",
        "criteria": {
            "true": "Interest rates, central banks, inflation, jobs, growth, trade and tariffs, "
            "energy, currencies, bonds, or broad market moves and their causes",
            "false": "A single company with no wider economic angle, or a topic unrelated to the economy",
        },
    },
}

PURPOSE = (
    "Neuralwire is a financial news platform for investors. Each day it publishes a short "
    "briefing on AI and a short briefing on the market backdrop, written from posts like this one."
)

WORTH = {
    "ai_daily": {
        "type": "noul",
        "instructions": f"{PURPOSE} Does this post add something meaningful to the AI briefing?",
        "criteria": {
            "true": "A notable model, product or research release, a significant move by an AI "
            "company, lab or regulator, or an informed insight that moves the AI discussion forward, "
            "including a lab announcing its own release",
            "false": "A joke, a reaction, a vague or personal musing, a usage tip, congratulations, "
            "engagement bait, or a post that only makes sense with context it does not give",
        },
    },
    "ai_daily_plain": {
        "type": "noul",
        "instructions": f"{PURPOSE} Does this post add something meaningful to the AI briefing?",
    },
    "macro_daily": {
        "type": "noul",
        "instructions": f"{PURPOSE} Does this post add something meaningful to the market-backdrop "
        "briefing?",
        "criteria": {
            "true": "New economic data, a central-bank decision or signal, a policy, trade or "
            "geopolitical event that moves markets, or an informed analysis of what is driving "
            "markets now",
            "false": "Chatter about a single company, routine price moves, vague predictions, hype, "
            "jokes, or promotion",
        },
    },
}


# The section's worth question with no criteria: purpose and question only.
WORTH_PLAIN = {
    "ai_daily": {
        "type": "noul",
        "instructions": f"{PURPOSE} Does this post add something meaningful to the AI briefing?",
    },
    "macro_daily": {
        "type": "noul",
        "instructions": f"{PURPOSE} Does this post add something meaningful to the market-backdrop "
        "briefing?",
    },
}


def _rank_v1(scores: dict, group: str) -> float:
    """Section fit x (news or substance, whichever is higher) x (1 - promo)."""
    fit = max(scores["ai"], scores["macro"]) if group == "wire" else scores.get(group, 0.0)
    return fit * max(scores["news"], scores["substance"]) * (1 - scores["promo"])


def _rank_worth(scores: dict, group: str) -> float:
    """The section's own worth question; wire posts take whichever section they fit better."""
    if group == "wire":
        return max(scores["ai_daily"], scores["macro_daily"])
    return scores["ai_daily"] if group == "ai" else scores["macro_daily"]


# Every question set ever stored, so old labels stay readable and comparable.
QSETS = {
    "v1": {"questions": SIGNALS, "rank": _rank_v1},
    "v2": {"questions": {**WORTH, **SIGNALS}, "rank": _rank_worth},
    "v3": {"questions": {**WORTH_PLAIN, **SIGNALS}, "rank": _rank_worth},
}
QSET = "v3"  # the set the daily job scores with and the feed shows
QUESTIONS = QSETS[QSET]["questions"]


def rank_score(scores: dict | None, group: str, qset: str = QSET) -> float | None:
    """One 0-1 number for sorting a post within its feed group, by the question set's rule."""
    if not scores:
        return None
    return QSETS[qset]["rank"](scores, group)


# ── Post text ────────────────────────────────────────────────────────────────


def readable_text(text: str | None, links: list[dict], media: list[dict]) -> str:
    """Text with t.co links expanded and media links removed (they are described separately)."""
    text = text or ""
    for link in links or []:
        if link.get("tco"):
            text = text.replace(link["tco"], link.get("display") or link.get("url") or "")
    for m in media or []:
        if m.get("tco"):
            text = text.replace(m["tco"], "")
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"[ \t]+\n", "\n", text).strip()


def describe_media(media: list[dict]) -> str:
    kinds = [m.get("type") for m in media or []]
    return ", ".join(f"{kinds.count(k)} {k.replace('_', ' ')}" for k in dict.fromkeys(kinds))


def post_state(post: dict) -> str:
    """What Jev reads for one post: author, text, attachments and any quoted post."""
    followers = post.get("author_followers")
    who = f"@{post['author_handle']}" + (f" ({followers:,} followers)" if followers else "")
    lines = [
        f"Author: {who}",
        f"Post: {readable_text(post.get('text'), post.get('links'), post.get('media'))}",
    ]
    if post.get("media"):
        lines.append(f"Attached: {describe_media(post['media'])}")
    q = post.get("quoted")
    if q:
        quoted = readable_text(q.get("text"), q.get("links"), q.get("media"))
        lines.append(f"Quoting @{q.get('author')}: {quoted}")
    return "\n".join(lines)


# ── Calls ────────────────────────────────────────────────────────────────────


def _ask(session: requests.Session, post: dict, questions: dict) -> dict:
    r = session.post(
        JEV_URL,
        json={"state": post_state(post), "model": JEV_MODEL, "questions": questions},
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"{r.status_code}: {r.text[:300]}")
    out = r.json()
    usage = out.get("usage") or {}
    tokens = usage.get("input_tokens") or 0
    return {
        "post_id": str(post["post_id"]),
        "scores": {k: float(v["noul"]) for k, v in out["answers"].items()},
        "input_tokens": tokens,
        "cost_usd": usage.get("cost")
        if usage.get("cost") is not None
        else tokens * JEV_PRICE_PER_M / 1e6,
    }


def label_posts(
    posts: list[dict],
    start: datetime | None,
    end: datetime | None,
    label: str,
    job: str,
    qset: str = QSET,
) -> dict:
    """
    Score `posts` (rows from x_store.posts_without_labels) with question set `qset` and
    archive the batch. start/end name the day the posts are from; None for a batch that
    spans days (the eval script's).
    Returns a record like the fetches: {"meta": {...}, "labels": [...], "failed": [post ids]}.
    Failed posts are left out, so the next run retries them.
    """
    key = os.environ.get("OPENROUTER_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_KEY is not set")
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {key}"

    labels, failed = [], []

    questions = QSETS[qset]["questions"]

    def one(post):
        try:
            return _ask(session, post, questions)
        except (RuntimeError, requests.RequestException, KeyError, ValueError) as e:
            print(f"  [jev] post {post['post_id']} failed: {e}")
            return None

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for post, result in zip(posts, pool.map(one, posts), strict=True):
            if result:
                labels.append(result)
            else:
                failed.append(str(post["post_id"]))

    record = {
        "meta": {
            "source": "jev",
            "job": job,
            "label": label,
            "query": f"{JEV_MODEL} qset={qset}",
            "qset": qset,
            "window_start": start.isoformat() if start else None,
            "window_end": end.isoformat() if end else None,
            "fetched_at": datetime.now(UTC).isoformat(),
            "latency_s": round(time.perf_counter() - t0, 2),
            "est_cost_usd": sum(lab["cost_usd"] for lab in labels),
        },
        "labels": labels,
        "failed": failed,
    }
    if labels:
        ids = sorted(lab["post_id"] for lab in labels)
        day = start or datetime.now(UTC)
        path = x_sources.archive_path("jev", day, f"{label}_{qset}", {"ids": ids})
        x_sources.write_archive(path, record)
    return record
