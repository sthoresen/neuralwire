"""
x_sources.py

Reads X posts through Apify (paid per post) and post counts through the X API
(counts/recent, which X does not bill). No database access here: x_store.py
writes, run_x_daily.py orchestrates.

Raw archive, local development only: with the local docker database, every
successful response is also saved as one JSON file under X_RAW_DIR (default
data/x_raw/, gitignored). run_x_refresh.py --rebuild reloads the local database
from it, and a request whose file already exists is answered from the file for
free. Production keeps no archive: its database is the record, and incremental
fetching never repeats a request.
"""

import hashlib
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

import db_connection

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = Path(os.environ.get("X_RAW_DIR", REPO_ROOT / "data" / "x_raw"))

APIFY_BASE = "https://api.apify.com/v2"
APIFY_ACTOR = "kaitoeasyapi~twitter-x-data-tweet-scraper-pay-per-result-cheapest"
APIFY_PRICE_PER_ITEM = 0.00025  # used when a run carries no pricingInfo
X_BASE = "https://api.x.com/2"

# The from:-clause of one query. X's API allows 512 characters per query, and
# the filters and time bounds need the rest.
MAX_CLAUSE_CHARS = 400

# counts/recent only reaches back 7 days; keep a margin for clock drift.
COUNTS_REACH = timedelta(days=6, hours=23)


# ── Queries ──────────────────────────────────────────────────────────────────


def chunk_handles(handles: list[str], max_chars: int = MAX_CLAUSE_CHARS) -> list[list[str]]:
    """
    Split handles into as few groups as fit max_chars per (from:a OR from:b ...) clause, with
    the groups as even as possible: every query is billed for at least 15 items, so a query
    holding one quiet account would pay mostly for filler.
    """
    greedy, current = [], []
    for h in handles:
        if current and len(from_clause(current + [h])) > max_chars:
            greedy.append(current)
            current = []
        current.append(h)
    if current:
        greedy.append(current)

    n = len(greedy)
    size, extra = divmod(len(handles), n)
    even, i = [], 0
    for k in range(n):
        step = size + (1 if k < extra else 0)
        even.append(handles[i : i + step])
        i += step
    if all(len(from_clause(c)) <= max_chars for c in even):
        return even
    return greedy


def from_clause(handles: list[str]) -> str:
    return "(" + " OR ".join(f"from:{h}" for h in handles) + ")"


def apify_query(handles: list[str], start: datetime, end: datetime) -> str:
    """X search syntax: original posts by these accounts in [start, end)."""
    return (
        f"{from_clause(handles)} -filter:retweets -filter:replies "
        f"since_time:{int(start.timestamp())} until_time:{int(end.timestamp())}"
    )


def x_counts_query(handles: list[str]) -> str:
    """X API v2 syntax for the same set of posts as apify_query (the window goes in params)."""
    return f"{from_clause(handles)} -is:retweet -is:reply"


# ── Raw archive ──────────────────────────────────────────────────────────────


def archive_path(source: str, start: datetime, label: str, request: dict) -> Path:
    """data/x_raw/<source>/<window day>/<label>_<hash of the request>.json"""
    key = hashlib.sha1(json.dumps(request, sort_keys=True, default=str).encode()).hexdigest()[:12]
    return RAW_DIR / source / start.strftime("%Y-%m-%d") / f"{label}_{key}.json"


def archiving() -> bool:
    """The raw archive is kept only alongside the local database (see the module docstring)."""
    return db_connection.is_local_db()


def write_archive(path: Path, record: dict) -> None:
    if not archiving():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record))
    except OSError as e:
        print(f"  [x_sources] WARNING: could not archive {path}: {e}")


def read_archive(path: Path) -> dict:
    return json.loads(path.read_text())


def archive_files() -> list[Path]:
    """Every archived response, oldest fetch first."""
    files = list(RAW_DIR.glob("*/*/*.json"))
    return sorted(files, key=lambda p: read_archive(p)["meta"]["fetched_at"])


def purge_archive(days: int) -> int:
    """
    Delete archived responses whose window started more than `days` ago: they hold post text,
    which is kept no longer than the database keeps it. Judgments are kept. Returns files removed.
    """
    cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
    removed = 0
    for day_dir in RAW_DIR.glob("*/*"):
        if day_dir.is_dir() and day_dir.name < cutoff:
            for f in day_dir.glob("*.json"):
                f.unlink()
                removed += 1
            day_dir.rmdir()
    return removed


JUDGMENTS_FILE = RAW_DIR / "judgments.jsonl"


def append_judgment(post_id: int, verdict: str | None) -> None:
    """Append one verdict from the feed page (None clears it). Replayed by --rebuild."""
    if not archiving():
        return
    line = {"post_id": post_id, "verdict": verdict, "at": datetime.now(UTC).isoformat()}
    try:
        JUDGMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with JUDGMENTS_FILE.open("a") as f:
            f.write(json.dumps(line) + "\n")
    except OSError as e:
        print(f"  [x_sources] WARNING: could not save judgment for {post_id}: {e}")


def latest_judgments() -> dict[int, str | None]:
    """post_id -> its most recent verdict, from the judgments file."""
    if not JUDGMENTS_FILE.exists():
        return {}
    out = {}
    for line in JUDGMENTS_FILE.read_text().splitlines():
        if line.strip():
            j = json.loads(line)
            out[j["post_id"]] = j["verdict"]
    return out


# ── Apify ────────────────────────────────────────────────────────────────────


def fetch_apify(
    handles: list[str],
    start: datetime,
    end: datetime,
    label: str,
    job: str,
    max_items: int = 250,
    max_charge_usd: float = 0.10,
    replay: bool = True,
) -> tuple[dict, bool]:
    """
    Run the Apify search for original posts by `handles` in [start, end).
    Returns (record, replayed). record = {"meta": {...}, "items": [raw items]}.
    Raises RuntimeError when the run fails; failures are not archived, so a
    later run retries them.
    """
    actor_input = {
        "twitterContent": apify_query(handles, start, end),
        "maxItems": max_items,
        "queryType": "Latest",
    }
    path = archive_path("apify", start, label, {"actor": APIFY_ACTOR, **actor_input})
    if replay and archiving() and path.exists():
        return read_archive(path), True

    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise RuntimeError("APIFY_TOKEN is not set")
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"

    t0 = time.perf_counter()
    r = session.post(
        f"{APIFY_BASE}/acts/{APIFY_ACTOR}/runs",
        params={"waitForFinish": 60, "maxTotalChargeUsd": max_charge_usd},
        json=actor_input,
        timeout=90,
    )
    if not r.ok:
        raise RuntimeError(f"Apify start failed, {r.status_code}: {r.text[:300]}")
    run = r.json()["data"]
    while run["status"] in ("READY", "RUNNING"):
        run = session.get(
            f"{APIFY_BASE}/actor-runs/{run['id']}", params={"waitForFinish": 60}, timeout=90
        ).json()["data"]
    if run["status"] != "SUCCEEDED":
        raise RuntimeError(f"Apify run {run['id']} ended with status {run['status']}")
    items = session.get(
        f"{APIFY_BASE}/datasets/{run['defaultDatasetId']}/items",
        params={"clean": "true"},
        timeout=60,
    ).json()

    record = {
        "meta": {
            "source": "apify",
            "job": job,
            "label": label,
            "query": actor_input["twitterContent"],
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "fetched_at": datetime.now(UTC).isoformat(),
            "latency_s": round(time.perf_counter() - t0, 2),
            "run_id": run["id"],
            "max_items": max_items,
            # Every returned item is billed, the mock_tweet filler included.
            "est_cost_usd": len(items) * _apify_item_price(run),
        },
        "items": items,
    }
    write_archive(path, record)
    return record, False


def _apify_item_price(run: dict) -> float:
    events = ((run.get("pricingInfo") or {}).get("pricingPerEvent") or {}).get(
        "actorChargeEvents"
    ) or {}
    item = events.get("apify-default-dataset-item") or {}
    return item.get("eventPriceUsd") or APIFY_PRICE_PER_ITEM


def parse_apify_items(items: list[dict], start: datetime, end: datetime) -> tuple[list[dict], dict]:
    """
    Turn raw Apify items into post rows for x_posts.
    Returns (posts, stats). stats counts what was left out:
      mock     filler items the actor adds to reach its minimum charge per query
      dropped  replies, reposts or posts outside [start, end) that the query let through
    """
    posts, stats = [], {"mock": 0, "dropped": 0}
    for it in items:
        if it.get("type") != "tweet":
            stats["mock"] += 1
            continue
        created = datetime.strptime(it["createdAt"], "%a %b %d %H:%M:%S %z %Y")
        if it.get("isReply") or it.get("retweeted_tweet") or not (start <= created < end):
            stats["dropped"] += 1
            continue
        author = it.get("author") or {}
        quoted = it.get("quoted_tweet") or {}
        posts.append(
            {
                "post_id": int(it["id"]),
                "author_handle": author.get("userName"),
                "author_name": author.get("name"),
                "author_id": str(author["id"]) if author.get("id") else None,
                "created_at": created,
                "text": it.get("text"),
                "url": it.get("url") or f"https://x.com/{author.get('userName')}/status/{it['id']}",
                "lang": it.get("lang"),
                "quoted_post_id": int(quoted["id"]) if quoted.get("id") else None,
                "likes": it.get("likeCount"),
                "reposts": it.get("retweetCount"),
                "replies": it.get("replyCount"),
                "quotes": it.get("quoteCount"),
                "views": it.get("viewCount"),
                "author_followers": author.get("followers"),
                "media": _media(it),
                "links": _links(it),
                "quoted": _quoted(quoted) if quoted.get("id") else None,
            }
        )
    return posts, stats


def _media(item: dict) -> list[dict]:
    """Photos, videos and GIFs: type, image (a thumbnail for videos), x.com URL, and the t.co in the text."""
    return [
        {
            "type": m.get("type"),
            "image": m.get("media_url_https"),
            "url": m.get("expanded_url"),
            "tco": m.get("url"),
        }
        for m in (item.get("extendedEntities") or {}).get("media") or []
    ]


def _links(item: dict) -> list[dict]:
    """Links in the text: the t.co short link with where it really goes."""
    return [
        {"tco": u.get("url"), "url": u.get("expanded_url"), "display": u.get("display_url")}
        for u in (item.get("entities") or {}).get("urls") or []
    ]


def _quoted(q: dict) -> dict:
    author = q.get("author") or {}
    return {
        "post_id": str(q["id"]),
        "author": author.get("userName"),
        "text": q.get("text"),
        "url": q.get("url"),
        "media": _media(q),
        "links": _links(q),
    }


# ── X API counts (not billed) ────────────────────────────────────────────────


def counts_in_reach(start: datetime) -> bool:
    """counts/recent can only answer windows that start within the last 7 days."""
    return start >= datetime.now(UTC) - COUNTS_REACH


def count_x(
    handles: list[str], start: datetime, end: datetime, label: str, job: str, replay: bool = True
) -> tuple[dict, bool]:
    """
    Number of original posts by `handles` in [start, end), from X's counts/recent.
    Used to check that the Apify fetch got everything. Returns (record, replayed);
    record["total"] is the count.
    """
    params = {
        "query": x_counts_query(handles),
        "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_time": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "granularity": "day",
    }
    path = archive_path("x_counts", start, label, params)
    if replay and archiving() and path.exists():
        return read_archive(path), True

    token = os.environ.get("X_BEARER_TOKEN")
    if not token:
        raise RuntimeError("X_BEARER_TOKEN is not set")
    r = requests.get(
        f"{X_BASE}/tweets/counts/recent",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"X counts failed, {r.status_code}: {r.text[:300]}")
    data = r.json()

    record = {
        "meta": {
            "source": "x_counts",
            "job": job,
            "label": label,
            "query": params["query"],
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "fetched_at": datetime.now(UTC).isoformat(),
            "est_cost_usd": 0.0,
        },
        "total": data.get("meta", {}).get("total_tweet_count", 0),
    }
    write_archive(path, record)
    return record, False
