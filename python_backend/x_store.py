"""
x_store.py

Postgres tables and queries for the X pipeline:
  x_accounts   the curated accounts, synced from x_accounts.py
  x_posts      one row per post; metrics are the latest read (metrics_at)
  x_runs       one row per API call or archive replay: what was asked, what came back, the cost
  x_labels     Jev's scores per post, one row per question-set version
  x_judgments  your own keep/skip verdicts from the feed page, to measure Jev against
  x_digests    the LLM-written briefings: per section, the last 24 hours or 7 days, as
               of the time they were written
"""

from psycopg2.extras import Json

import db_connection
from db_connection import get_conn
from x_accounts import Account


def is_local_db() -> bool:
    """True when the configured database is the local docker one, never production."""
    return db_connection.is_local_db()


def init_tables() -> None:
    """Create the X tables if missing. Safe to call on every run."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS x_accounts (
        handle     TEXT PRIMARY KEY,
        author_id  TEXT,
        group_name TEXT NOT NULL,
        active     BOOLEAN NOT NULL DEFAULT TRUE,
        added_at   TIMESTAMPTZ DEFAULT now()
    )
    """)
    c.execute("ALTER TABLE x_accounts DROP COLUMN IF EXISTS kind")
    c.execute("""
    CREATE TABLE IF NOT EXISTS x_runs (
        id           SERIAL PRIMARY KEY,
        job          TEXT NOT NULL,
        source       TEXT NOT NULL,
        label        TEXT,
        query        TEXT,
        window_start TIMESTAMPTZ,
        window_end   TIMESTAMPTZ,
        items        INTEGER,
        mock_items   INTEGER,
        dropped      INTEGER,
        est_cost_usd NUMERIC(10, 5),
        replayed     BOOLEAN NOT NULL DEFAULT FALSE,
        fetched_at   TIMESTAMPTZ,
        recorded_at  TIMESTAMPTZ DEFAULT now(),
        error        TEXT
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS x_posts (
        post_id          BIGINT PRIMARY KEY,
        author_handle    TEXT NOT NULL,
        author_id        TEXT,
        created_at       TIMESTAMPTZ NOT NULL,
        text             TEXT,
        url              TEXT NOT NULL,
        lang             TEXT,
        quoted_post_id   BIGINT,
        likes            INTEGER,
        reposts          INTEGER,
        replies          INTEGER,
        quotes           INTEGER,
        views            BIGINT,
        author_followers INTEGER,
        metrics_at       TIMESTAMPTZ,
        source           TEXT NOT NULL,
        first_run_id     INTEGER REFERENCES x_runs(id),
        last_run_id      INTEGER REFERENCES x_runs(id)
    )
    """)
    for col in ("media", "links", "quoted"):  # added after the first version of the table
        c.execute(f"ALTER TABLE x_posts ADD COLUMN IF NOT EXISTS {col} JSONB")
    c.execute("ALTER TABLE x_posts ADD COLUMN IF NOT EXISTS author_name TEXT")
    c.execute(
        "CREATE INDEX IF NOT EXISTS x_posts_author_created ON x_posts (lower(author_handle), created_at)"
    )
    c.execute("""
    CREATE TABLE IF NOT EXISTS x_labels (
        post_id      BIGINT NOT NULL,
        qset         TEXT NOT NULL,
        scores       JSONB NOT NULL,
        input_tokens INTEGER,
        cost_usd     NUMERIC(12, 8),
        run_id       INTEGER REFERENCES x_runs(id),
        labeled_at   TIMESTAMPTZ DEFAULT now(),
        PRIMARY KEY (post_id, qset)
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS x_digests (
        id            SERIAL PRIMARY KEY,
        edition_at    TIMESTAMPTZ NOT NULL,  -- when it was written; the window ends here
        edition_date  DATE NOT NULL,         -- edition_at's date in New York, for the date picker
        window_start  TIMESTAMPTZ NOT NULL,
        window_end    TIMESTAMPTZ NOT NULL,
        section       TEXT NOT NULL,
        period        TEXT NOT NULL CHECK (period IN ('day', 'week')),
        content       JSONB NOT NULL,  -- daily {"title", "paragraphs", "also", "post_ids"},
                                       -- weekly {"title", "stories", "post_ids"}
        candidate_ids BIGINT[] NOT NULL,
        model_name    TEXT,
        prompt_id     TEXT,
        prompt        TEXT
    )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS x_digests_latest ON x_digests (section, period, edition_at)"
    )
    c.execute("""
    CREATE TABLE IF NOT EXISTS x_judgments (
        post_id    BIGINT PRIMARY KEY,
        verdict    TEXT NOT NULL CHECK (verdict IN ('keep', 'skip')),
        judged_at  TIMESTAMPTZ DEFAULT now()
    )
    """)
    conn.commit()
    conn.close()


def sync_accounts(accounts: list[Account]) -> None:
    """Upsert the listed accounts as active; mark every other stored account inactive."""
    conn = get_conn()
    c = conn.cursor()
    for a in accounts:
        c.execute(
            """
            INSERT INTO x_accounts (handle, group_name, active) VALUES (%s, %s, TRUE)
            ON CONFLICT (handle) DO UPDATE SET group_name = EXCLUDED.group_name, active = TRUE
            """,
            (a.handle, a.group),
        )
    c.execute(
        "UPDATE x_accounts SET active = FALSE WHERE handle <> ALL(%s)",
        ([a.handle for a in accounts],),
    )
    conn.commit()
    conn.close()


def record_run(meta: dict, items: int, mock: int, dropped: int, replayed: bool, error=None) -> int:
    """Log one API call or archive replay in x_runs. A replay costs nothing, so it logs $0."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO x_runs (job, source, label, query, window_start, window_end, items,
                            mock_items, dropped, est_cost_usd, replayed, fetched_at, error)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            meta["job"],
            meta["source"],
            meta.get("label"),
            meta.get("query"),
            meta.get("window_start"),
            meta.get("window_end"),
            items,
            mock,
            dropped,
            0.0 if replayed else meta.get("est_cost_usd", 0.0),
            replayed,
            meta.get("fetched_at"),
            error,
        ),
    )
    run_id = c.fetchone()[0]
    conn.commit()
    conn.close()
    return run_id


def upsert_posts(posts: list[dict], run_id: int, source: str = "apify") -> None:
    """Insert new posts; for known posts, refresh text, metrics and attachments to this read."""
    if not posts:
        return
    conn = get_conn()
    c = conn.cursor()
    for p in posts:
        c.execute(
            """
            INSERT INTO x_posts (post_id, author_handle, author_name, author_id, created_at,
                                 text, url, lang,
                                 quoted_post_id, likes, reposts, replies, quotes, views,
                                 author_followers, media, links, quoted, metrics_at, source,
                                 first_run_id, last_run_id)
            VALUES (%(post_id)s, %(author_handle)s, %(author_name)s, %(author_id)s,
                    %(created_at)s, %(text)s,
                    %(url)s, %(lang)s, %(quoted_post_id)s, %(likes)s, %(reposts)s, %(replies)s,
                    %(quotes)s, %(views)s, %(author_followers)s, %(media)s, %(links)s,
                    %(quoted)s, (SELECT fetched_at FROM x_runs WHERE id = %(run_id)s),
                    %(source)s, %(run_id)s, %(run_id)s)
            ON CONFLICT (post_id) DO UPDATE SET
                author_name = EXCLUDED.author_name,
                text = EXCLUDED.text, likes = EXCLUDED.likes, reposts = EXCLUDED.reposts,
                replies = EXCLUDED.replies, quotes = EXCLUDED.quotes, views = EXCLUDED.views,
                author_followers = EXCLUDED.author_followers, media = EXCLUDED.media,
                links = EXCLUDED.links, quoted = EXCLUDED.quoted,
                metrics_at = EXCLUDED.metrics_at, last_run_id = EXCLUDED.last_run_id
            """,
            {
                **p,
                "author_name": p.get("author_name"),
                "media": Json(p.get("media") or []),
                "links": Json(p.get("links") or []),
                "quoted": Json(p["quoted"]) if p.get("quoted") else None,
                "run_id": run_id,
                "source": source,
            },
        )
    # Handles can be renamed; the author id can't. Keep it on the account.
    c.execute(
        """
        UPDATE x_accounts a SET author_id = p.author_id
          FROM (SELECT DISTINCT lower(author_handle) AS h, author_id
                  FROM x_posts WHERE last_run_id = %s AND author_id IS NOT NULL) p
         WHERE lower(a.handle) = p.h AND a.author_id IS DISTINCT FROM p.author_id
        """,
        (run_id,),
    )
    conn.commit()
    conn.close()


def clear_for_rebuild() -> None:
    """
    Empty the tables the raw archive can recreate (posts, runs, Jev labels).
    Accounts come from x_accounts.py; judgments are replayed from their own file.
    """
    conn = get_conn()
    c = conn.cursor()
    c.execute("TRUNCATE x_labels, x_judgments, x_posts, x_runs RESTART IDENTITY")
    conn.commit()
    conn.close()


# ── Jev labels ───────────────────────────────────────────────────────────────


def posts_without_labels(start, end, qset: str) -> list[dict]:
    """Posts created in [start, end) by listed accounts that have no labels for this question set."""
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()
    c.execute(
        """
        SELECT p.post_id, p.author_handle, p.author_followers, p.text, p.media, p.links, p.quoted
          FROM x_posts p
          JOIN x_accounts a ON lower(a.handle) = lower(p.author_handle)
         WHERE p.created_at >= %s AND p.created_at < %s
           AND NOT EXISTS (SELECT 1 FROM x_labels l WHERE l.post_id = p.post_id AND l.qset = %s)
        """,
        (start, end, qset),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def save_labels(labels: list[dict], qset: str, run_id: int) -> None:
    """labels: [{"post_id", "scores", "input_tokens", "cost_usd"}]. Re-labelling overwrites."""
    conn = get_conn()
    c = conn.cursor()
    for lab in labels:
        c.execute(
            """
            INSERT INTO x_labels (post_id, qset, scores, input_tokens, cost_usd, run_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (post_id, qset) DO UPDATE SET
                scores = EXCLUDED.scores, input_tokens = EXCLUDED.input_tokens,
                cost_usd = EXCLUDED.cost_usd, run_id = EXCLUDED.run_id, labeled_at = now()
            """,
            (
                int(lab["post_id"]),
                qset,
                Json(lab["scores"]),
                lab.get("input_tokens"),
                lab.get("cost_usd"),
                run_id,
            ),
        )
    conn.commit()
    conn.close()


# ── Judgments ────────────────────────────────────────────────────────────────


def set_judgment(post_id: int, verdict: str | None) -> None:
    """Store your keep/skip verdict for a post; None clears it."""
    conn = get_conn()
    c = conn.cursor()
    if verdict is None:
        c.execute("DELETE FROM x_judgments WHERE post_id = %s", (post_id,))
    else:
        c.execute(
            """
            INSERT INTO x_judgments (post_id, verdict) VALUES (%s, %s)
            ON CONFLICT (post_id) DO UPDATE SET verdict = EXCLUDED.verdict, judged_at = now()
            """,
            (post_id, verdict),
        )
    conn.commit()
    conn.close()


def judged_posts(qset: str) -> list[dict]:
    """Every judged post with its group, likes and Jev scores (None if not labelled)."""
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()
    c.execute(
        """
        SELECT j.post_id, j.verdict, a.group_name, p.author_handle, p.likes, p.text, l.scores
          FROM x_judgments j
          JOIN x_posts p ON p.post_id = j.post_id
          JOIN x_accounts a ON lower(a.handle) = lower(p.author_handle)
          LEFT JOIN x_labels l ON l.post_id = j.post_id AND l.qset = %s
        """,
        (qset,),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def judged_without_labels(qset: str) -> list[dict]:
    """Judged posts with no labels for this question set, in the shape x_jev.label_posts takes."""
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()
    c.execute(
        """
        SELECT p.post_id, p.author_handle, p.author_followers, p.text, p.media, p.links, p.quoted
          FROM x_judgments j
          JOIN x_posts p ON p.post_id = j.post_id
         WHERE NOT EXISTS (SELECT 1 FROM x_labels l WHERE l.post_id = j.post_id AND l.qset = %s)
        """,
        (qset,),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# ── Feed page ────────────────────────────────────────────────────────────────


def days_with_posts(limit: int = 60) -> list[dict]:
    """UTC days that have stored posts, newest first: [{"date", "posts"}]."""
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()
    c.execute(
        """
        SELECT (created_at AT TIME ZONE 'UTC')::date AS date, count(*) AS posts
          FROM x_posts GROUP BY 1 ORDER BY 1 DESC LIMIT %s
        """,
        (limit,),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


_POST_SELECT = """
        SELECT p.post_id::text AS post_id, p.author_handle, p.author_name, p.created_at,
               p.text, p.url,
               p.likes, p.reposts, p.replies, p.quotes, p.views, p.author_followers,
               p.media, p.links, p.quoted, a.group_name, a.active,
               l.scores, j.verdict AS judgment
          FROM x_posts p
          JOIN x_accounts a ON lower(a.handle) = lower(p.author_handle)
          LEFT JOIN x_labels l ON l.post_id = p.post_id AND l.qset = %s
          LEFT JOIN x_judgments j ON j.post_id = p.post_id"""


def posts_between(start, end, qset: str) -> list[dict]:
    """Posts created in [start, end) by listed accounts, with group, Jev scores and your verdict."""
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()
    c.execute(
        f"""{_POST_SELECT}
         WHERE p.created_at >= %s AND p.created_at < %s
         ORDER BY p.likes DESC NULLS LAST
        """,
        (qset, start, end),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def runs_for_window(start, end) -> list[dict]:
    """The original fetch, count and labelling run per label for one window, for the coverage line."""
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()
    c.execute(
        """
        SELECT DISTINCT ON (source, label) source, label, items, mock_items, est_cost_usd,
               replayed, fetched_at, error
          FROM x_runs
         WHERE window_start = %s AND window_end = %s
         ORDER BY source, label, replayed, id DESC  -- the original fetch, not a replay
        """,
        (start, end),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def account_day_report(start, end) -> list[tuple]:
    """Per active account, posts created in [start, end): (group, handle, posts, median likes, max likes)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT a.group_name, a.handle, count(p.post_id),
               percentile_cont(0.5) WITHIN GROUP (ORDER BY p.likes), max(p.likes)
          FROM x_accounts a
          LEFT JOIN x_posts p
            ON lower(p.author_handle) = lower(a.handle)
           AND p.created_at >= %s AND p.created_at < %s
         WHERE a.active
         GROUP BY a.group_name, a.handle
         ORDER BY a.group_name, count(p.post_id) DESC, a.handle
        """,
        (start, end),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def posts_by_ids(ids: list[int], qset: str) -> list[dict]:
    """The same rows as posts_between, for specific posts (the ones a digest cites)."""
    if not ids:
        return []
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()
    c.execute(f"{_POST_SELECT}\n WHERE p.post_id = ANY(%s)", (qset, list(ids)))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# ── Digests ──────────────────────────────────────────────────────────────────

_DIGEST_COLS = """edition_at, edition_date, window_start, window_end, section, period, content,
                  candidate_ids, model_name, prompt_id"""


def save_digest(digest: dict) -> None:
    """digest: edition_at, window_start, window_end, section, period, content, candidate_ids,
    model_name, prompt_id, prompt. edition_date is derived (New York date of edition_at)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO x_digests (edition_at, edition_date, window_start, window_end, section,
                               period, content, candidate_ids, model_name, prompt_id, prompt)
        VALUES (%(edition_at)s, (%(edition_at)s AT TIME ZONE 'America/New_York')::date,
                %(window_start)s, %(window_end)s, %(section)s, %(period)s, %(content)s,
                %(candidate_ids)s, %(model_name)s, %(prompt_id)s, %(prompt)s)
        """,
        {**digest, "content": Json(digest["content"])},
    )
    conn.commit()
    conn.close()


def latest_digest(section: str, period: str, until=None) -> dict | None:
    """The newest digest for a section and period written at or before `until` (default: now)."""
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()
    c.execute(
        f"""
        SELECT {_DIGEST_COLS} FROM x_digests
         WHERE section = %s AND period = %s AND edition_at <= coalesce(%s, now())
         ORDER BY edition_at DESC LIMIT 1
        """,
        (section, period, until),
    )
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def daily_digests_between(start, end, section: str) -> list[dict]:
    """The last daily digest of each New York date written in [start, end], oldest first."""
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()
    c.execute(
        f"""
        SELECT DISTINCT ON (edition_date) {_DIGEST_COLS} FROM x_digests
         WHERE section = %s AND period = 'day' AND edition_at BETWEEN %s AND %s
         ORDER BY edition_date, edition_at DESC
        """,
        (section, start, end),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def edition_times(limit: int = 300) -> list:
    """When daily digests were written, newest first. The page groups them into the reader's dates."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT DISTINCT edition_at FROM x_digests WHERE period = 'day' "
        "ORDER BY edition_at DESC LIMIT %s",
        (limit,),
    )
    rows = [r[0] for r in c.fetchall()]
    conn.close()
    return rows


# ── Refresh bookkeeping ──────────────────────────────────────────────────────


def last_fetched_until(label: str):
    """Where the last successful Apify fetch for this query label ended, or None."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT max(window_end) FROM x_runs
         WHERE source = 'apify' AND label = %s AND error IS NULL
        """,
        (label,),
    )
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def purge_old_text(days: int = 30) -> int:
    """
    Remove the text and attachments of posts older than `days` (X's rules on keeping
    content); ids, authors and numbers stay for the stats. Returns the posts cleared.
    """
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        UPDATE x_posts SET text = NULL, quoted = NULL, media = '[]', links = '[]'
         WHERE created_at < now() - %s * interval '1 day' AND text IS NOT NULL
        """,
        (days,),
    )
    n = c.rowcount
    conn.commit()
    conn.close()
    return n
