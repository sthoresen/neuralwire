import json

from db_connection import get_conn


def init_db():
    """
    Initializes the database with the new Pipeline Architecture.
    - 'summaries': Historical context (1993-2025).
    - 'articles': Registry of all fetched news (Lightweight).
    - 'article_content': Heavy text storage (Scraped data).
    - 'analysis_runs': AI insights and scores.
    """
    conn = get_conn()
    c = conn.cursor()

    # A. Historical Summaries (Your Bootstrap Data)
    c.execute('''
    CREATE TABLE IF NOT EXISTS summaries (
        id           SERIAL PRIMARY KEY,
        ticker       TEXT,
        period_type  TEXT,
        period_value TEXT,
        content      TEXT,
        short_content TEXT,
        sources      TEXT,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # B. Articles Registry (Fast Metadata)
    c.execute('''
    CREATE TABLE IF NOT EXISTS articles (
        id              SERIAL PRIMARY KEY,
        url             TEXT UNIQUE,
        ticker          TEXT,
        headline        TEXT,
        provider        TEXT,
        api_summary     TEXT,
        published_at    TIMESTAMP,
        scrape_status   TEXT DEFAULT 'pending',
        analysis_status TEXT DEFAULT 'pending',
        scrape_error    TEXT,
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # C. Article Content (Heavy Storage)
    c.execute('''
    CREATE TABLE IF NOT EXISTS article_content (
        article_id    INTEGER PRIMARY KEY,
        raw_full_text TEXT,
        curated_text  TEXT,
        scraped_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(article_id) REFERENCES articles(id)
    )
    ''')

    # D. Analysis Runs (AI Insights)
    c.execute('''
    CREATE TABLE IF NOT EXISTS analysis_runs (
        id                  SERIAL PRIMARY KEY,
        article_id          INTEGER,
        ticker              TEXT,
        model_name          TEXT,
        prompt_id           TEXT,
        prompt              TEXT,
        impact_headline     TEXT,
        reasoning           TEXT,
        ai_summary          TEXT,
        relevancy_score     INTEGER,
        breaking_news_score INTEGER,
        importance_score    INTEGER,
        sentiment_score     REAL,
        run_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(article_id) REFERENCES articles(id)
    )
    ''')

    # E. Ticker Artefacts (Meta-Analyses for UI boxes)
    c.execute('''
    CREATE TABLE IF NOT EXISTS ticker_artefacts (
        id            SERIAL PRIMARY KEY,
        ticker        TEXT NOT NULL,
        artefact_type TEXT NOT NULL,
        content       TEXT NOT NULL,
        model_name    TEXT,
        prompt_id     TEXT,
        prompt        TEXT,
        generated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # F. Ticker Events (Structured, append-only event log)
    c.execute('''
    CREATE TABLE IF NOT EXISTS ticker_events (
        id               SERIAL PRIMARY KEY,
        ticker           TEXT NOT NULL,
        title            TEXT NOT NULL,
        event_date       TEXT,
        event_date_label TEXT,
        description      TEXT NOT NULL,
        sources          TEXT,
        article_ids      TEXT,
        model_name       TEXT,
        created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # G. Earnings Anchor
    c.execute('''
    CREATE TABLE IF NOT EXISTS earnings (
        id           SERIAL PRIMARY KEY,
        ticker       TEXT NOT NULL,
        report_date  DATE NOT NULL,
        period_label TEXT,
        period_start DATE,
        period_end   DATE,
        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(ticker, report_date)
    )
    ''')

    # H. Earnings Documents
    c.execute('''
    CREATE TABLE IF NOT EXISTS earnings_documents (
        id                SERIAL PRIMARY KEY,
        earnings_id       INTEGER NOT NULL REFERENCES earnings(id),
        doc_type          TEXT NOT NULL,
        content           TEXT NOT NULL,
        quality_score     INTEGER DEFAULT 50,
        is_preferred      BOOLEAN DEFAULT FALSE,
        source_url        TEXT,
        source_article_id INTEGER REFERENCES articles(id),
        created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # I. Earnings Reactions
    c.execute('''
    CREATE TABLE IF NOT EXISTS earnings_reactions (
        id                       SERIAL PRIMARY KEY,
        earnings_id              INTEGER NOT NULL UNIQUE REFERENCES earnings(id),
        beats_eps                BOOLEAN,
        beats_revenue            BOOLEAN,
        guidance_direction       TEXT,
        reaction_summary         TEXT,
        analyst_commentary       TEXT,
        contributing_article_ids TEXT,
        model_name               TEXT,
        generated_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # J. App Settings (key/value config store)
    c.execute('''
    CREATE TABLE IF NOT EXISTS app_settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    ''')

    conn.commit()
    conn.close()
    print("Database pipeline structure initialized.")


def init_ticker_artefacts_table():
    """
    Standalone migration: creates the ticker_artefacts table on an existing DB.
    Safe to call multiple times (IF NOT EXISTS).
    """
    conn = get_conn()
    c = conn.cursor()
    c.execute('''
    CREATE TABLE IF NOT EXISTS ticker_artefacts (
        id            SERIAL PRIMARY KEY,
        ticker        TEXT NOT NULL,
        artefact_type TEXT NOT NULL,
        content       TEXT NOT NULL,
        model_name    TEXT,
        prompt_id     TEXT,
        prompt        TEXT,
        generated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    conn.commit()
    conn.close()
    print("ticker_artefacts table ready.")


def migrate_add_event_check_status():
    """
    Adds event_check_status column to analysis_runs.
    Safe to call multiple times (ADD COLUMN IF NOT EXISTS).
    """
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "ALTER TABLE analysis_runs ADD COLUMN IF NOT EXISTS event_check_status TEXT DEFAULT 'pending'"
    )
    conn.commit()
    conn.close()
    print("event_check_status column ensured on analysis_runs.")


def get_unchecked_analyses(ticker, limit=50):
    """
    Returns analysis_runs for ticker that have not yet been scanned for events,
    joined with article metadata. Oldest first so we process chronologically.
    """
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()
    c.execute('''
        SELECT ar.id, ar.article_id, ar.ticker,
               ar.impact_headline, ar.ai_summary,
               a.headline, a.published_at, a.url
        FROM analysis_runs ar
        JOIN articles a ON ar.article_id = a.id
        WHERE ar.ticker = %s
          AND (ar.event_check_status = 'pending' OR ar.event_check_status IS NULL)
        ORDER BY a.published_at ASC
        LIMIT %s
    ''', (ticker, limit))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def mark_analyses_event_checked(ids):
    """Marks a list of analysis_run IDs as checked for events."""
    if not ids:
        return
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute(
            "UPDATE analysis_runs SET event_check_status = 'checked' WHERE id = ANY(%s)",
            (list(ids),)
        )
        conn.commit()
    finally:
        conn.close()


def migrate_add_earnings_tables():
    """
    Standalone migration: creates ticker_events, earnings, earnings_documents,
    and earnings_reactions on an existing DB. Safe to call multiple times.
    """
    conn = get_conn()
    c = conn.cursor()

    c.execute('''
    CREATE TABLE IF NOT EXISTS ticker_events (
        id               SERIAL PRIMARY KEY,
        ticker           TEXT NOT NULL,
        title            TEXT NOT NULL,
        event_date       TEXT,
        event_date_label TEXT,
        description      TEXT NOT NULL,
        sources          TEXT,
        article_ids      TEXT,
        model_name       TEXT,
        created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    c.execute('''
    CREATE TABLE IF NOT EXISTS earnings (
        id           SERIAL PRIMARY KEY,
        ticker       TEXT NOT NULL,
        report_date  DATE NOT NULL,
        period_label TEXT,
        period_start DATE,
        period_end   DATE,
        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(ticker, report_date)
    )
    ''')

    c.execute('''
    CREATE TABLE IF NOT EXISTS earnings_documents (
        id                SERIAL PRIMARY KEY,
        earnings_id       INTEGER NOT NULL REFERENCES earnings(id),
        doc_type          TEXT NOT NULL,
        content           TEXT NOT NULL,
        quality_score     INTEGER DEFAULT 50,
        is_preferred      BOOLEAN DEFAULT FALSE,
        source_url        TEXT,
        source_article_id INTEGER REFERENCES articles(id),
        created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    c.execute('''
    CREATE TABLE IF NOT EXISTS earnings_reactions (
        id                       SERIAL PRIMARY KEY,
        earnings_id              INTEGER NOT NULL UNIQUE REFERENCES earnings(id),
        beats_eps                BOOLEAN,
        beats_revenue            BOOLEAN,
        guidance_direction       TEXT,
        reaction_summary         TEXT,
        analyst_commentary       TEXT,
        contributing_article_ids TEXT,
        model_name               TEXT,
        generated_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    conn.commit()
    conn.close()
    print("Earnings tables migration complete.")


def insert_raw_article(ticker, data):
    """
    Fast insert for the fetcher.
    data expected: {'url', 'title', 'source', 'summary', 'time_published'}
    Returns article_id if new, None if duplicate.
    """
    conn = get_conn()
    c = conn.cursor()
    article_id = None

    try:
        c.execute("SELECT id FROM articles WHERE url = %s", (data.get('url'),))
        row = c.fetchone()

        if row:
            return None  # Duplicate

        c.execute('''
            INSERT INTO articles (url, ticker, headline, provider, api_summary, published_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        ''', (
            data.get('url'),
            ticker,
            data.get('title'),
            data.get('source'),
            data.get('summary'),
            data.get('time_published'),
        ))
        article_id = c.fetchone()[0]
        conn.commit()
    except Exception as e:
        print(f"Insert error: {e}")
    finally:
        conn.close()

    return article_id


def save_analysis_result(article_id, ticker, model, prompt_id, prompt,
                         impact_headline, reasoning, summary, relevance, breaking, importance, full_text=None):
    """
    Saves a ticker-specific AI analysis to the database.
    """
    if not summary or len(summary.strip()) < 10:
        print(f"      [DB] Skipping save for {ticker}: Summary is empty or too short.")
        return False

    if not impact_headline or relevance is None:
        print(f"      [DB] Skipping save for {ticker}: Missing critical metrics.")
        return False

    conn = get_conn()
    c = conn.cursor()

    try:
        if full_text:
            c.execute('''
                INSERT INTO article_content (article_id, curated_text)
                VALUES (%s, %s)
                ON CONFLICT (article_id) DO NOTHING
            ''', (article_id, full_text))

        c.execute('''
            INSERT INTO analysis_runs (
                article_id, ticker, model_name, prompt_id, prompt,
                impact_headline, reasoning, ai_summary, relevancy_score, breaking_news_score, importance_score
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            article_id, ticker, model, prompt_id, prompt,
            impact_headline, reasoning, summary, relevance, breaking, importance
        ))

        c.execute("UPDATE articles SET analysis_status = 'success' WHERE id = %s", (article_id,))

        conn.commit()
        return True
    except Exception as e:
        print(f"Error saving analysis for article {article_id}: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def visualize_db():
    """
    Prints a detailed overview of the database pipeline status.
    """
    conn = get_conn()
    c = conn.cursor()

    print("\n" + "="*60)
    print(" PIPELINE HEALTH REPORT")
    print("="*60)

    c.execute("SELECT COUNT(*) FROM articles")
    total_articles = c.fetchone()[0]
    print(f"Total Articles in Registry: {total_articles}")

    c.execute("SELECT scrape_status, COUNT(*) FROM articles GROUP BY scrape_status")
    scrape_stats = dict(c.fetchall())

    s_p = scrape_stats.get('pending', 0)
    s_s = scrape_stats.get('success', 0)
    s_f = scrape_stats.get('failed', 0)

    print(f"\n[SCRAPING STAGE]")
    print(f"{'  - Scraped Successfully:':<30} {s_s}")
    print(f"{'  - Scraping Failed:':<30} {s_f}")
    print(f"{'  - Scraping Pending:':<30} {s_p}")

    c.execute("SELECT analysis_status, COUNT(*) FROM articles GROUP BY analysis_status")
    analysis_stats = dict(c.fetchall())

    a_p = analysis_stats.get('pending', 0)
    a_s = analysis_stats.get('success', 0)
    a_f = analysis_stats.get('failed', 0)

    print(f"\n[AI ANALYSIS STAGE]")
    print(f"{'  - Analyzed Successfully:':<30} {a_s}")
    print(f"{'  - Analysis Failed:':<30} {a_f}")
    print(f"{'  - Analysis Pending:':<30} {a_p}")

    c.execute('''
        SELECT COUNT(*) FROM articles
        WHERE scrape_status = 'failed' AND analysis_status = 'success'
    ''')
    fallback_count = c.fetchone()[0]

    c.execute('''
        SELECT COUNT(*) FROM articles
        WHERE scrape_status = 'success' AND analysis_status = 'pending'
    ''')
    bottleneck_count = c.fetchone()[0]

    print(f"\n[PIPELINE INSIGHTS]")
    print(f"  - Analyzed via API summary only (Scrape failed): {fallback_count}")
    print(f"  - Scraped successfully but awaiting AI:        {bottleneck_count}")

    c.execute('''
        SELECT prompt_id, COUNT(*)
        FROM analysis_runs
        GROUP BY prompt_id
        ORDER BY COUNT(*) DESC
    ''')
    prompts = c.fetchall()

    if prompts:
        print("\n[PROMPT BREAKDOWN]")
        print(f"{'Prompt ID':<30} | {'Analysis Count'}")
        print("-" * 50)
        for p_id, count in prompts:
            display_id = str(p_id) if p_id else "[Unknown/None]"
            print(f"{display_id:<30} | {count}")
    else:
        print("\n[PROMPT BREAKDOWN] No analysis records found yet.")

    print("="*60 + "\n")
    conn.close()


def get_article_text(article_id):
    """Fetches the full curated text for an article ID."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT raw_full_text FROM article_content WHERE article_id = %s", (article_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def mark_scrape_success_and_save(article_id, full_text):
    """
    Saves the full scraped text and updates the article status to 'success'.
    """
    conn = get_conn()
    c = conn.cursor()

    try:
        c.execute('''
            INSERT INTO article_content (article_id, raw_full_text, scraped_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (article_id) DO UPDATE SET
                raw_full_text = EXCLUDED.raw_full_text,
                scraped_at    = CURRENT_TIMESTAMP
        ''', (article_id, full_text))

        c.execute('''
            UPDATE articles
            SET scrape_status = 'success',
                scrape_error  = NULL
            WHERE id = %s
        ''', (article_id,))

        conn.commit()
    except Exception as e:
        print(f"Error marking scrape success for ID {article_id}: {e}")
        conn.rollback()
    finally:
        conn.close()


def mark_scrape_failed(article_id, error_msg):
    """
    Updates the article status to 'failed' and logs the error message.
    """
    conn = get_conn()
    c = conn.cursor()

    try:
        c.execute('''
            UPDATE articles
            SET scrape_status = 'failed',
                scrape_error  = %s
            WHERE id = %s
        ''', (error_msg, article_id))
        conn.commit()
    except Exception as e:
        print(f"Error logging scrape failure for ID {article_id}: {e}")
    finally:
        conn.close()


def mark_analysis_done(article_id):
    """Marks an article as analyzed even if no insights were found."""
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("UPDATE articles SET analysis_status = 'success' WHERE id = %s", (article_id,))
        conn.commit()
    finally:
        conn.close()


def get_active_tickers() -> list[str]:
    """Returns the list of tickers the system actively tracks, ordered alphabetically."""
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT DISTINCT ticker FROM ticker_artefacts
            WHERE artefact_type = 'header_description'
            ORDER BY ticker
        """)
        rows = c.fetchall()
        conn.close()
        return [r[0] for r in rows] if rows else ["NVDA"]
    except Exception as e:
        print(f"WARNING: could not query active tickers ({e}), falling back to NVDA")
        return ["NVDA"]


def get_pending_articles(limit=20):
    """
    Fetches articles that haven't been analyzed yet, newest first.
    """
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()
    c.execute('''
        SELECT * FROM articles
        WHERE analysis_status = 'pending'
        ORDER BY published_at DESC
        LIMIT %s
    ''', (limit,))
    rows = [dict(row) for row in c.fetchall()]
    conn.close()
    return rows


def mark_articles_skipped(article_ids: list[int], reason: str = "pre_filter") -> int:
    """
    Marks articles as skipped so they are excluded from future analysis runs.
    Returns the number of rows updated.
    """
    if not article_ids:
        return 0
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute(
            "UPDATE articles SET analysis_status = %s WHERE id = ANY(%s)",
            (f"skipped:{reason}", article_ids),
        )
        updated = c.rowcount
        conn.commit()
        return updated
    except Exception as e:
        print(f"mark_articles_skipped error: {e}")
        conn.rollback()
        return 0
    finally:
        conn.close()


def save_ticker_artefact(ticker, artefact_type, content, model_name, prompt_id, prompt):
    """
    Appends a new artefact row for the given ticker and type.
    Does not overwrite — history is preserved.
    """
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute('''
            INSERT INTO ticker_artefacts (ticker, artefact_type, content, model_name, prompt_id, prompt)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', (ticker, artefact_type, content, model_name, prompt_id, prompt))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error saving artefact ({ticker}/{artefact_type}): {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def get_ticker_artefact(ticker, artefact_type):
    """
    Returns the most recently generated artefact for the given ticker and type,
    or None if none exists.
    """
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()
    c.execute('''
        SELECT * FROM ticker_artefacts
        WHERE ticker = %s AND artefact_type = %s
        ORDER BY generated_at DESC
        LIMIT 1
    ''', (ticker, artefact_type))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


# ── Ticker Events ─────────────────────────────────────────────────────────────

def insert_ticker_event(ticker, title, description, event_date=None,
                        event_date_label=None, sources=None,
                        article_ids=None, model_name=None):
    """
    Appends a new event to the ticker event log.
    sources and article_ids should be Python lists; they are stored as JSON.
    """
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute('''
            INSERT INTO ticker_events
                (ticker, title, event_date, event_date_label, description,
                 sources, article_ids, model_name)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        ''', (
            ticker, title, event_date, event_date_label, description,
            json.dumps(sources or []),
            json.dumps(article_ids or []),
            model_name,
        ))
        conn.commit()
        return c.fetchone()[0]
    except Exception as e:
        print(f"Error inserting event ({ticker}): {e}")
        conn.rollback()
        return None
    finally:
        conn.close()


def bulk_insert_ticker_events(ticker, events, model_name=None, skip_if_exists=True):
    """
    Inserts a list of event dicts in a single transaction.
    skip_if_exists: if True and the ticker already has events, aborts and returns 0.
    Returns the number of rows inserted.
    """
    if skip_if_exists:
        conn = get_conn()
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM ticker_events WHERE ticker = %s', (ticker,))
        count = c.fetchone()[0]
        conn.close()
        if count > 0:
            print(f"[bulk_insert] Skipping — {count} events already exist for {ticker}. "
                  f"Pass skip_if_exists=False to force.")
            return 0

    conn = get_conn()
    c = conn.cursor()
    inserted = 0
    try:
        for ev in events:
            c.execute('''
                INSERT INTO ticker_events
                    (ticker, title, event_date, event_date_label, description,
                     sources, article_ids, model_name)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ''', (
                ticker,
                ev.get('title', '').strip(),
                ev.get('event_date'),
                ev.get('event_date_label'),
                ev.get('description', '').strip(),
                json.dumps(ev.get('sources') or []),
                json.dumps(ev.get('article_ids') or []),
                model_name,
            ))
            inserted += 1
        conn.commit()
        print(f"[bulk_insert] Inserted {inserted} events for {ticker}.")
        return inserted
    except Exception as e:
        print(f"[bulk_insert] Error: {e}")
        conn.rollback()
        return 0
    finally:
        conn.close()


def get_ticker_events(ticker, limit=None):
    """
    Returns all events for a ticker ordered by event_date DESC (most recent first).
    sources and article_ids are returned as Python lists.
    """
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()
    query = '''
        SELECT * FROM ticker_events
        WHERE ticker = %s
        ORDER BY event_date DESC
    '''
    params = [ticker]
    if limit:
        query += ' LIMIT %s'
        params.append(limit)
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()

    result = []
    for row in rows:
        r = dict(row)
        r['sources'] = json.loads(r['sources'] or '[]')
        r['article_ids'] = json.loads(r['article_ids'] or '[]')
        result.append(r)
    return result


# ── Earnings ───────────────────────────────────────────────────────────────────

def get_or_create_earnings(ticker, report_date, period_label=None,
                           period_start=None, period_end=None):
    """
    Returns the earnings.id for (ticker, report_date), creating the row if needed.
    report_date: ISO string, e.g. '2024-02-21'.
    Safe to call multiple times — UNIQUE constraint prevents duplicates.
    """
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute('''
            INSERT INTO earnings (ticker, report_date, period_label, period_start, period_end)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT(ticker, report_date) DO UPDATE SET
                period_label = COALESCE(EXCLUDED.period_label, earnings.period_label),
                period_start = COALESCE(EXCLUDED.period_start, earnings.period_start),
                period_end   = COALESCE(EXCLUDED.period_end,   earnings.period_end)
            RETURNING id
        ''', (ticker, report_date, period_label, period_start, period_end))
        conn.commit()
        return c.fetchone()[0]
    except Exception as e:
        print(f"Error in get_or_create_earnings ({ticker}/{report_date}): {e}")
        conn.rollback()
        return None
    finally:
        conn.close()


def get_earnings_list(ticker, limit=10):
    """Returns recent earnings events for a ticker, newest first."""
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()
    c.execute('''
        SELECT * FROM earnings
        WHERE ticker = %s
        ORDER BY report_date DESC
        LIMIT %s
    ''', (ticker, limit))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_latest_earnings(ticker):
    """Returns the most recent earnings row for a ticker, or None."""
    rows = get_earnings_list(ticker, limit=1)
    return rows[0] if rows else None


def save_earnings_document(earnings_id, doc_type, content, quality_score=50,
                           is_preferred=False, source_url=None,
                           source_article_id=None):
    """
    Saves a document (transcript, press_release, presentation) linked to an earnings event.
    Multiple documents per earnings are allowed.
    """
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute('''
            INSERT INTO earnings_documents
                (earnings_id, doc_type, content, quality_score, is_preferred,
                 source_url, source_article_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        ''', (earnings_id, doc_type, content, quality_score, is_preferred,
              source_url, source_article_id))
        conn.commit()
        return c.fetchone()[0]
    except Exception as e:
        print(f"Error saving earnings document (earnings_id={earnings_id}): {e}")
        conn.rollback()
        return None
    finally:
        conn.close()


def get_earnings_documents(earnings_id, doc_type=None):
    """
    Returns all documents for an earnings event, ordered by quality_score DESC.
    Optionally filter by doc_type ('transcript', 'press_release', 'presentation').
    """
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()
    if doc_type:
        c.execute('''
            SELECT * FROM earnings_documents
            WHERE earnings_id = %s AND doc_type = %s
            ORDER BY quality_score DESC
        ''', (earnings_id, doc_type))
    else:
        c.execute('''
            SELECT * FROM earnings_documents
            WHERE earnings_id = %s
            ORDER BY quality_score DESC
        ''', (earnings_id,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_best_transcript(earnings_id):
    """Returns the highest-quality transcript for an earnings event, or None."""
    docs = get_earnings_documents(earnings_id, doc_type='transcript')
    return docs[0] if docs else None


def upsert_earnings_reaction(earnings_id, reaction_summary, analyst_commentary,
                             contributing_article_ids, model_name,
                             beats_eps=None, beats_revenue=None,
                             guidance_direction=None):
    """
    Creates or replaces the earnings reaction for a given earnings event.
    contributing_article_ids: Python list of article IDs.
    """
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute('''
            INSERT INTO earnings_reactions
                (earnings_id, beats_eps, beats_revenue, guidance_direction,
                 reaction_summary, analyst_commentary,
                 contributing_article_ids, model_name, generated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT(earnings_id) DO UPDATE SET
                beats_eps                = EXCLUDED.beats_eps,
                beats_revenue            = EXCLUDED.beats_revenue,
                guidance_direction       = EXCLUDED.guidance_direction,
                reaction_summary         = EXCLUDED.reaction_summary,
                analyst_commentary       = EXCLUDED.analyst_commentary,
                contributing_article_ids = EXCLUDED.contributing_article_ids,
                model_name               = EXCLUDED.model_name,
                generated_at             = CURRENT_TIMESTAMP
        ''', (
            earnings_id, beats_eps, beats_revenue, guidance_direction,
            reaction_summary, analyst_commentary,
            json.dumps(contributing_article_ids or []),
            model_name,
        ))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error upserting earnings reaction (earnings_id={earnings_id}): {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def delete_earnings_documents(earnings_id: int, doc_type: str) -> int:
    """Deletes all documents of a given type for an earnings event. Returns count deleted."""
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute('DELETE FROM earnings_documents WHERE earnings_id = %s AND doc_type = %s',
                  (earnings_id, doc_type))
        conn.commit()
        return c.rowcount
    finally:
        conn.close()


def get_earnings_press_release(ticker: str, earnings_id: int) -> str | None:
    """
    Returns a formatted string containing the earnings press release for the
    given earnings event, ready to drop into an LLM prompt as context.
    Returns None if no press release document is stored.
    """
    e = get_full_earnings(ticker, earnings_id=earnings_id)
    if not e:
        return None

    pr_docs = [d for d in e.get('documents', []) if d['doc_type'] == 'press_release']
    if not pr_docs:
        return None

    pr = pr_docs[0]
    period_range = (f"{e.get('period_start', '?')} to {e.get('period_end', '?')}"
                    if e.get('period_start') else 'N/A')
    header = (
        f"EARNINGS PRESS RELEASE\n"
        f"Ticker      : {ticker}\n"
        f"Period      : {e.get('period_label', 'N/A')}\n"
        f"Report date : {e.get('report_date', 'N/A')}\n"
        f"Covers      : {period_range}\n"
        f"{'─' * 60}\n"
    )
    return header + pr['content']


def get_focal_points(ticker):
    """Returns the most recently generated focal_points artefact for the ticker, or None."""
    return get_ticker_artefact(ticker, "focal_points")


def get_monthly_news_flow(ticker):
    """
    Returns the most recently generated monthly_news_flow artefact for the ticker,
    or None if none exists.
    """
    return get_ticker_artefact(ticker, "monthly_news_flow")


def get_earnings_reaction(earnings_id):
    """Returns the reaction for an earnings event, or None."""
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()
    c.execute('SELECT * FROM earnings_reactions WHERE earnings_id = %s', (earnings_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    r = dict(row)
    r['contributing_article_ids'] = json.loads(r['contributing_article_ids'] or '[]')
    return r


def get_full_earnings(ticker, report_date=None, earnings_id=None):
    """
    Returns a single earnings event with all its documents and reaction attached.
    Pass either report_date (ISO string) or earnings_id.
    """
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()

    if earnings_id:
        c.execute('SELECT * FROM earnings WHERE id = %s', (earnings_id,))
    elif report_date:
        c.execute('SELECT * FROM earnings WHERE ticker = %s AND report_date = %s',
                  (ticker, report_date))
    else:
        c.execute('SELECT * FROM earnings WHERE ticker = %s ORDER BY report_date DESC LIMIT 1',
                  (ticker,))

    row = c.fetchone()
    conn.close()
    if not row:
        return None

    e = dict(row)
    e['documents'] = get_earnings_documents(e['id'])
    e['reaction']  = get_earnings_reaction(e['id'])
    return e


# ── Ticker colors ─────────────────────────────────────────────────────────────

def init_ticker_colors_table() -> None:
    """Create the ticker_colors table if it doesn't exist."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS ticker_colors (
            ticker     TEXT PRIMARY KEY,
            hex_color  TEXT NOT NULL,
            source     TEXT NOT NULL DEFAULT 'brand',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def get_ticker_color(ticker: str) -> str | None:
    """Return the stored hex color for a ticker, or None if not yet set."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT hex_color FROM ticker_colors WHERE ticker = %s", (ticker,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def set_ticker_color(ticker: str, hex_color: str, source: str = "manual") -> None:
    """
    Store or update a ticker's accent color.
    Source priority: manual(3) > brand(2) > generated(1).
    Lower-priority sources never overwrite higher-priority ones.
    """
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO ticker_colors (ticker, hex_color, source, updated_at)
        VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT(ticker) DO UPDATE SET
            hex_color  = EXCLUDED.hex_color,
            source     = EXCLUDED.source,
            updated_at = CURRENT_TIMESTAMP
        WHERE
            CASE ticker_colors.source WHEN 'manual' THEN 3 WHEN 'brand' THEN 2 ELSE 1 END
            <=
            CASE EXCLUDED.source      WHEN 'manual' THEN 3 WHEN 'brand' THEN 2 ELSE 1 END
    """, (ticker, hex_color, source))
    conn.commit()
    conn.close()


def init_ticker_classification_table() -> None:
    """Create the ticker_classification table if it doesn't exist."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS ticker_classification (
            ticker        TEXT PRIMARY KEY,
            long_name     TEXT,
            exchange      TEXT,
            gics_sector   TEXT,
            gics_industry TEXT,
            display_tags  TEXT,
            fetched_at    TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def get_ticker_classification(ticker: str) -> dict | None:
    """Return stored classification for a ticker, or None if not cached."""
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()
    c.execute("SELECT * FROM ticker_classification WHERE ticker = %s", (ticker,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def save_ticker_classification(ticker: str, data: dict) -> None:
    """Upsert classification for a ticker."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO ticker_classification
            (ticker, long_name, exchange, gics_sector, gics_industry,
             display_tags, fetched_at)
        VALUES
            (%(ticker)s, %(long_name)s, %(exchange)s, %(gics_sector)s, %(gics_industry)s,
             %(display_tags)s, CURRENT_TIMESTAMP)
        ON CONFLICT(ticker) DO UPDATE SET
            long_name     = EXCLUDED.long_name,
            exchange      = EXCLUDED.exchange,
            gics_sector   = EXCLUDED.gics_sector,
            gics_industry = EXCLUDED.gics_industry,
            display_tags  = EXCLUDED.display_tags,
            fetched_at    = CURRENT_TIMESTAMP
    """, {"ticker": ticker, **data})
    conn.commit()
    conn.close()


def get_setting(key: str, default: str = None) -> str | None:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM app_settings WHERE key = %s", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else default


def set_setting(key: str, value: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO app_settings (key, value) VALUES (%s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """, (key, value))
    conn.commit()
    conn.close()


def inspect_analyses(ticker=None, limit=10):
    """
    Prints a detailed view of the actual AI analysis results.
    Can be filtered by ticker and limited to the most recent runs.
    """
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()

    query = '''
        SELECT
            ar.ticker,
            a.headline,
            ar.impact_headline,
            ar.reasoning,
            ar.ai_summary,
            ar.relevancy_score,
            ar.importance_score,
            ar.model_name
        FROM analysis_runs ar
        JOIN articles a ON ar.article_id = a.id
    '''

    params = []
    if ticker:
        query += " WHERE ar.ticker = %s"
        params.append(ticker)

    query += " ORDER BY ar.run_at DESC LIMIT %s"
    params.append(limit)

    c.execute(query, params)
    rows = c.fetchall()
    conn.close()

    if not rows:
        print(f"No analysis records found" + (f" for {ticker}" if ticker else ""))
        return

    print("\n" + "="*80)
    print(f" INSPECTING RECENT ANALYSES (Showing {len(rows)})")
    print("="*80)

    for i, row in enumerate(rows):
        print(f"\n[{i+1}] TICKER: {row['ticker']} | MODEL: {row['model_name']}")
        print(f"RAW HEADLINE: {row['headline']}")
        print(f"AI IMPACT:   {row['impact_headline']}")
        print(f"{'-'*30}")
        print(f"REASONING:   {row['reasoning']}")
        print(f"RELEVANCE:   {row['relevancy_score']}/100")
        print(f"IMPORTANCE:  {row['importance_score']}/100")
        print(f"{'-'*30}")

        summary_text = row['ai_summary'] if row['ai_summary'] is not None else ""

        if summary_text:
            summary_snippet = (summary_text[:250] + "...") if len(summary_text) > 250 else summary_text
        else:
            summary_snippet = "[No summary available]"

        print(f"AI SUMMARY:  {summary_snippet}")
        print("-" * 80)

    print("="*80 + "\n")
