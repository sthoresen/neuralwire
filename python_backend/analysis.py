#analysis.py

import re
import time
from dataclasses import dataclass, field

import news_reporter
import database
import prompts
import llms
import utils
from db_connection import get_conn

_NOISE_PATTERNS = [
    re.compile(r'\bstock[s]?\s+(up|down|rose|fell|drops?|gains?|surges?|tumbles?|climbs?|slides?|plunges?|rallies?)\s+\d', re.I),
    re.compile(r'\bshares?\s+(up|down|rose|fell|drops?|gains?|surges?|tumbles?|climbs?|slides?|plunges?|rallies?)\s+\d', re.I),
    re.compile(r'\btop\s+\d+\s+(stocks?|picks?|names?|companies)', re.I),
    re.compile(r'\b\d+\s+(stocks?|shares?|names?)\s+to\s+(watch|buy|avoid|consider|own)', re.I),
    re.compile(r'\bstocks?\s+to\s+(watch|buy|avoid|consider|own)', re.I),
    re.compile(r'\bbest\s+\d+\s+stocks?', re.I),
    re.compile(r'\bwhy\s+.{0,50}(could|might|may)\s+(rise|fall|rally|drop|surge|tumble)', re.I),
    re.compile(r'\binsider\s+(buys?|sells?|purchased|sold)\s+[\d,]+\s+shares?', re.I),
    re.compile(r'\b(etf|index)\s+(adds?|removes?|rebalances?|reconstitut)', re.I),
    re.compile(r'\bweekly\s+(recap|roundup|summary|wrap)', re.I),
    re.compile(r'\b(morning|afternoon|evening)\s+(brief|briefing|wrap|roundup)', re.I),
]

@dataclass
class AnalysisResult:
    """Standardizes the output from the LLM layer."""
    ticker: str
    data: dict
    model_name: str
    prompt_text: str
    prompt_id: str
    success: bool = True
    error_msg: str = ""


@dataclass
class RunStats:
    start_time: float = field(default_factory=time.time)
    batch_size: int = 0
    regex_skipped: int = 0
    llm_skipped: int = 0
    passed: int = 0
    scrape_attempted: int = 0
    scrape_success: int = 0
    scrape_failed: int = 0
    no_content: int = 0
    no_insight: int = 0
    llm_error: int = 0
    saved: dict = field(default_factory=dict)       # {ticker: count}
    models_used: dict = field(default_factory=dict)  # {model_name: count}


def generate_context_prompt(ticker="NVDA"):
    """
    Builds the Master Context string for your AI Agent.
    Combines: Master History + Latest 5-Year + Detailed Recent Years + Breaking News
    """
    conn = get_conn()
    c = conn.cursor()

    # A. Master Summary
    c.execute("SELECT content FROM summaries WHERE ticker=%s AND period_type='master'", (ticker,))
    row = c.fetchone()
    pm = row[0] if row else "No master summary available."

    # B. Latest 5-Year Era
    c.execute('''
        SELECT period_value, content FROM summaries
        WHERE ticker=%s AND period_type='five_year'
        ORDER BY period_value DESC LIMIT 1
    ''', (ticker,))
    p5_row = c.fetchone()
    p5_text = f"Period {p5_row[0]}: {p5_row[1]}" if p5_row else "No 5-year summary available."

    # C. Latest 3 Years (Detailed)
    c.execute('''
        SELECT period_value, short_content, content FROM summaries
        WHERE ticker=%s AND period_type='yearly'
        ORDER BY period_value DESC LIMIT 3
    ''', (ticker,))
    p3_string = ""
    for year, short, extended in c.fetchall():
        text = short if short else (extended[:500] + "...")
        p3_string += f"\n[{year}]\n{text}\n"

    # D. Breaking News (From Analysis Runs)
    # Fetches high-relevance (>80) AI summaries from the last 7 days
    c.execute('''
        SELECT ar.ai_summary, a.headline, ar.relevancy_score
        FROM analysis_runs ar
        JOIN articles a ON ar.article_id = a.id
        WHERE a.ticker=%s
          AND ar.relevancy_score > 80
          AND a.published_at > NOW() - INTERVAL '7 days'
        ORDER BY ar.relevancy_score DESC, a.published_at DESC
        LIMIT 5
    ''', (ticker,))

    news_rows = c.fetchall()
    news_section = ""
    if news_rows:
        news_section = "\n=== BREAKING NEWS (Last 7 Days) ===\n"
        for summary, headline, score in news_rows:
            news_section += f"- [Score {score}] {headline}: {summary[:200]}...\n"

    conn.close()

    return (
        f"=== MASTER HISTORY ===\n{pm}\n\n"
        f"=== CURRENT ERA ({ticker}) ===\n{p5_text}\n\n"
        f"=== RECENT ANNUAL PERFORMANCE ===\n{p3_string}"
        f"{news_section}"
    )

def _build_monthly_context(ticker: str, lookback_days: int = 30) -> dict:
    """
    Assembles the four context blocks fed into the monthly news flow prompt.
    Returns a dict with keys: recent_events, recent_articles, earnings_context, yearly_context.
    """
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()

    # 1. Recent ticker events (last ~60 days — wider window so events aren't empty)
    c.execute('''
        SELECT event_date, event_date_label, title, description
        FROM ticker_events
        WHERE ticker = %s
          AND event_date >= TO_CHAR(NOW() - INTERVAL '60 days', 'YYYY-MM-DD')
        ORDER BY event_date DESC
        LIMIT 15
    ''', (ticker,))
    event_rows = c.fetchall()
    if event_rows:
        recent_events = "\n".join(
            f"- [{r['event_date_label'] or r['event_date']}] {r['title']}: {r['description']}"
            for r in event_rows
        )
    else:
        recent_events = "(no events logged in the last 60 days)"

    # 2. High-relevance article summaries (last N days, score > 50, top 10)
    c.execute('''
        SELECT a.headline, a.published_at, ar.impact_headline, ar.ai_summary, ar.relevancy_score
        FROM analysis_runs ar
        JOIN articles a ON ar.article_id = a.id
        WHERE ar.ticker = %s
          AND a.published_at >= NOW() - (%s * INTERVAL '1 day')
          AND ar.relevancy_score > 50
        ORDER BY ar.relevancy_score DESC, a.published_at DESC
        LIMIT 10
    ''', (ticker, lookback_days))
    article_rows = c.fetchall()
    if article_rows:
        recent_articles = "\n".join(
            f"- [{str(r['published_at'])[:10]}] {r['impact_headline']}: {(r['ai_summary'] or '')[:200]}"
            for r in article_rows
        )
    else:
        recent_articles = "(no high-relevance articles in the last 30 days)"

    # 3. Latest earnings + reaction (if within ~6 months)
    c.execute('''
        SELECT e.period_label, e.report_date,
               er.beats_eps, er.beats_revenue, er.guidance_direction,
               er.reaction_summary
        FROM earnings e
        LEFT JOIN earnings_reactions er ON er.earnings_id = e.id
        WHERE e.ticker = %s
          AND e.report_date >= NOW() - INTERVAL '180 days'
        ORDER BY e.report_date DESC
        LIMIT 1
    ''', (ticker,))
    eq = c.fetchone()
    if eq:
        beats = []
        if eq['beats_eps'] is not None:
            beats.append(f"EPS {'beat' if eq['beats_eps'] else 'miss'}")
        if eq['beats_revenue'] is not None:
            beats.append(f"revenue {'beat' if eq['beats_revenue'] else 'miss'}")
        beat_str = ", ".join(beats) if beats else "beat/miss unknown"
        guidance = eq['guidance_direction'] or "unknown"
        summary = (eq['reaction_summary'] or "").strip()
        earnings_context = (
            f"{eq['period_label']} reported {eq['report_date']} — "
            f"{beat_str}, guidance {guidance}."
            + (f" {summary}" if summary else "")
        )
    else:
        earnings_context = "(no recent earnings within 6 months)"

    # 4. Most recent yearly summary as backdrop
    c.execute('''
        SELECT period_value, short_content, content
        FROM summaries
        WHERE ticker = %s AND period_type = 'yearly'
        ORDER BY period_value DESC
        LIMIT 1
    ''', (ticker,))
    yr = c.fetchone()
    if yr:
        text = yr['short_content'] or (yr['content'] or '')[:400]
        yearly_context = f"[{yr['period_value']}] {text}"
    else:
        yearly_context = "(no yearly summary available)"

    conn.close()

    return {
        "recent_events":   recent_events,
        "recent_articles": recent_articles,
        "earnings_context": earnings_context,
        "yearly_context":  yearly_context,
    }


def generate_focal_points(ticker: str = "NVDA", commit: bool = True) -> str:
    """
    Generates a 'Focal Points' briefing for the ticker — key strategic themes and
    analyst focus areas derived from the latest earnings transcript + monthly news flow.
    Saves the result as a ticker_artefact (type='focal_points') and returns the text.
    """
    PROMPT_ID = "focal_points_prompt_v1"

    # 1. Latest earnings + transcript (full content — transcripts are long and valuable)
    earnings = database.get_latest_earnings(ticker)
    if earnings:
        period_label = earnings.get("period_label", "N/A")
        doc = database.get_best_transcript(earnings["id"])
        transcript = doc["content"] if doc else None
    else:
        period_label = "N/A"
        transcript = None

    if not transcript:
        transcript = "(no earnings transcript available)"

    # 2. Monthly news flow
    mnf_artefact = database.get_monthly_news_flow(ticker)
    monthly_news_flow = mnf_artefact["content"] if mnf_artefact else "(no monthly news flow available)"

    prompt = prompts.focal_points_prompt.format(
        ticker=ticker,
        period_label=period_label,
        transcript=transcript,
        monthly_news_flow=monthly_news_flow,
    )

    print(f"[focal_points] Calling LLM for {ticker} ({period_label})...")
    llm_manager = llms.LLMProviderManager()
    content, model_name = llm_manager.call(prompt, tier="standard", reasoning=False)

    if not content:
        raise RuntimeError(f"[focal_points] All LLM providers failed for {ticker}")

    content = content.strip()
    print(f"[focal_points] {ticker}: {len(content)} chars from {model_name}")

    if commit:
        database.save_ticker_artefact(
            ticker=ticker,
            artefact_type="focal_points",
            content=content,
            model_name=model_name,
            prompt_id=PROMPT_ID,
            prompt=prompt,
        )

    return content


def generate_monthly_news_flow(ticker: str = "NVDA", lookback_days: int = 30,
                               commit: bool = True) -> str:
    """
    Generates a 2-3 paragraph 'Monthly News Flow' prose summary for the ticker.
    Uses Grok with live search on top of DB context.
    Saves the result as a ticker_artefact (type='monthly_news_flow') and returns the text.
    """
    PROMPT_ID = "monthly_news_flow_prompt_v1"

    ctx = _build_monthly_context(ticker, lookback_days)

    prompt = prompts.monthly_news_flow_prompt.format(
        ticker=ticker,
        **ctx,
    )

    print(f"[monthly_news_flow] Calling Grok with search for {ticker}...")
    llm_manager = llms.LLMProviderManager()
    content, model_name = llm_manager.call_grok_with_search(prompt)
    content = content.strip()

    print(f"[monthly_news_flow] {ticker}: {len(content)} chars from {model_name}")

    if commit:
        database.save_ticker_artefact(
            ticker=ticker,
            artefact_type="monthly_news_flow",
            content=content,
            model_name=model_name,
            prompt_id=PROMPT_ID,
            prompt=prompt,
        )

    return content


def generate_ticker_header(ticker="NVDA"):
    """
    Generates a single formal sentence describing what the company does,
    sourced from the master summary in the DB. Uses Grok exclusively — no fallbacks.
    Saves the result to ticker_artefacts and returns the content string.
    """
    PROMPT_ID = "generate_header_description_prompt_v1.1"

    # 1. Fetch master summary
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT content FROM summaries WHERE ticker=%s AND period_type='master'", (ticker,))
    row = c.fetchone()
    conn.close()

    if not row or not row[0]:
        raise ValueError(f"No master summary found for ticker '{ticker}'. Cannot generate header.")

    master_summary = row[0]

    # 2. Build prompt
    prompt = prompts.generate_header_description_prompt.format(master_summary=master_summary)

    # 3. Call Grok — raises RuntimeError if unavailable
    llm_manager = llms.LLMProviderManager()
    content, model_name = llm_manager.call(prompt, tier="standard", reasoning=False)

    content = content.strip()
    print(f"[generate_ticker_header] {ticker}: {content}")

    # 4. Persist
    database.save_ticker_artefact(
        ticker=ticker,
        artefact_type="header_description",
        content=content,
        model_name=model_name,
        prompt_id=PROMPT_ID,
        prompt=prompt,
    )

    return content


def analyze_article_impact(article):
    """
    Determines if we need a 'Master Split' (find tickers first) 
    or a 'Single Shot' (analyze immediately).

    Then calls llms to analyze the article
    """

    llm_manager = llms.LLMProviderManager()

    # Use the best available content
    if article.get('full_text'):
        #content = article['full_text'][:35000] # Cap to save tokens
        content = article['full_text']
        src = "Full Scraped Text"
    else:
        content = f"Headline: {article['headline']}\nSummary: {article['api_summary']}"
        src = "API Summary (Scrape unavailable)"

    url = article.get('url')
    results_container = []

    # --- STRATEGY A: Divide into multiple LLM calls ---
    if src == "Full Scraped Text" and len(content) > 1000:
        # Step 1: Identify Tickers
        print('analyze_article_impact: Using master prompt to divide work per ticker')
        prompt_master = prompts.ticker_identify_prompt.format(
        src=src, 
        url=url, 
        content=content
        )

        ticker_csv, model_used = llm_manager.call(prompt_master, tier="economy", max_tier="standard", reasoning=False)

        if not model_used:
            print('LLM unavailable for ticker identification.')
            return None  # Hard failure — all providers down

        print(f'ticker_csv_response={ticker_csv}')
        print(f'model used={model_used}')

        tickers = utils.csv_to_tickers(ticker_csv)
        if not tickers or tickers == -1 or len(tickers) == 0:
            return []  # LLM succeeded but article is boring/irrelevant

        # Filter to active watchlist — skip tickers we don't track
        active = _get_active_tickers()
        if active:
            before = tickers
            tickers = [t for t in tickers if t in active]
            dropped = [t for t in before if t not in active]
            if dropped:
                print(f'  [watchlist filter] dropped {dropped}, keeping {tickers}')
            if not tickers:
                return []

        # Step 2: Analyze per Ticker
        for ticker in tickers:
            prompt = prompts.analyze_article_impact_prompt_single_ticker_v1.format(
            src=src,
            url=url,
            ticker=ticker,
            content=content
            )
            try:
                res_text, model_used = llm_manager.call(prompt, tier="economy", max_tier="standard", reasoning=False)
                data = utils.clean_json_response(res_text)

                # Normalise: LLM may return a list despite being asked for one ticker
                if isinstance(data, list):
                    # Prefer the entry matching the expected ticker; fall back to first
                    data = next((d for d in data if isinstance(d, dict) and d.get('ticker') == ticker), data[0] if data else None)

                print(f'data={data}')
                if data and isinstance(data, dict):
                    data['ticker'] = ticker  # Force consistency
                    results_container.append(AnalysisResult(
                        ticker=ticker,
                        data=data,
                        model_name=model_used,
                        prompt_text=prompt,
                        prompt_id="analyze_article_impact_prompt_single_ticker_v1"
                    ))
            except Exception as e:
                print(f"LLM Error [{ticker}]: {e}")
                # Continue to next ticker — one bad parse shouldn't abort the article

    # --- STRATEGY B: SHORT CONTENT (Single Shot) ---
    else:
        # One LLM call that does all the work
        print('analyze_article_impact: Using one llm call to do all the work')
        prompt = prompts.analyze_article_impact_prompt_v1.format(
            src=src, 
            url=url, 
            content=content
        )
        
        try:
            res_text, model_used = llm_manager.call(prompt, tier="economy", max_tier="standard", reasoning=False)
            data = utils.clean_json_response(res_text)
            # Handle case where LLM returns a LIST of objects vs a single object
            if isinstance(data, list):
                items = data
            else:
                items = [data]

            for item in items:
                if item and 'ticker' in item:
                    results_container.append(AnalysisResult(
                        ticker=item['ticker'],
                        data=item,
                        model_name=model_used,
                        prompt_text=prompt,
                        prompt_id="analyze_article_impact_prompt_v1"
                    ))

        except Exception as e:
            print(f"LLM Error: {e}")
            return None  # Hard failure

    return results_container


#Find an url in the db and run an analysis on that only
def analyze_url(target_url):
    """
    Fetches a single article from the DB by URL and runs the analysis on it.
    Useful for testing specific articles.
    """
    conn = get_conn(dict_cursor=True)
    c = conn.cursor()

    # 1. Fetch the article object from the DB
    c.execute("SELECT * FROM articles WHERE url = %s", (target_url,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        print(f"❌ URL not found in database: {target_url}")
        return
        
    article = dict(row)
    print(f"🚀 Starting manual analysis for: {article.get('headline')[:50]}...")
    
    # 2. Run analysis
    # We wrap the single article in a list because the function expects a list
    #return run_multi_ticker_analysis_with_save([article])
    return run_analysis_pipeline([article])


def _is_regex_noise(headline: str) -> bool:
    return any(p.search(headline) for p in _NOISE_PATTERNS)


def _is_llm_noise(headline: str, api_summary: str) -> bool:
    """Returns True if the LLM judges the article as not worth analyzing. Fails open (returns False) on LLM error."""
    prompt = prompts.prefilter_prompt.format(
        headline=headline,
        summary=(api_summary or "")[:500],
    )
    result, _ = llms.llm_manager.call(prompt, tier="economy", max_tier="economy", reasoning=False, max_tokens=5)
    if not result:
        return False  # LLM unavailable — let it through
    return result.strip().upper().startswith("NO")


def _run_pre_filter(articles: list[dict], stats: RunStats = None) -> list[dict]:
    """
    Removes obvious noise before scraping or full LLM analysis.
    Stage 1: free regex on headline.
    Stage 2: cheap LLM call on headline + api_summary.
    Returns the articles that should proceed to full analysis.
    """
    keep, regex_skip, llm_skip = [], [], []

    for article in articles:
        headline = article.get("headline") or ""
        if _is_regex_noise(headline):
            regex_skip.append(article["id"])
        else:
            keep.append(article)

    if regex_skip:
        database.mark_articles_skipped(regex_skip, reason="regex")
        print(f"  [pre-filter] regex skipped {len(regex_skip)} articles")

    survivors, llm_skip_ids = [], []
    for article in keep:
        headline = article.get("headline") or ""
        api_summary = article.get("api_summary") or ""
        if _is_llm_noise(headline, api_summary):
            llm_skip_ids.append(article["id"])
        else:
            survivors.append(article)

    if llm_skip_ids:
        database.mark_articles_skipped(llm_skip_ids, reason="llm_prefilter")
        print(f"  [pre-filter] LLM skipped {len(llm_skip_ids)} articles")

    print(f"  [pre-filter] {len(survivors)}/{len(articles)} articles proceed to full analysis")

    if stats is not None:
        stats.regex_skipped = len(regex_skip)
        stats.llm_skipped = len(llm_skip_ids)
        stats.passed = len(survivors)

    return survivors


def _get_active_tickers() -> set[str]:
    return set(database.get_active_tickers())


def _print_run_summary(stats: RunStats):
    conn = get_conn()
    c = conn.cursor()

    # Backlog: articles still pending after this run
    c.execute("SELECT COUNT(*) FROM articles WHERE analysis_status = 'pending'")
    backlog = c.fetchone()[0]

    # Last analysis timestamp per active watchlist tickers only
    c.execute("""
        SELECT ar.ticker, MAX(ar.run_at)
        FROM analysis_runs ar
        WHERE ar.ticker IN (
            SELECT DISTINCT ticker FROM ticker_artefacts
            WHERE artefact_type = 'header_description'
        )
        GROUP BY ar.ticker
        ORDER BY ar.ticker
    """)
    last_per_ticker = c.fetchall()
    conn.close()

    elapsed = time.time() - stats.start_time
    mins, secs = divmod(int(elapsed), 60)
    duration_str = f"{mins}m {secs:02d}s"

    total_saved = sum(stats.saved.values())
    saved_parts = "  ".join(f"{t}={n}" for t, n in sorted(stats.saved.items()))
    saved_str = f"{saved_parts}  (total={total_saved})" if saved_parts else "none"

    models_str = "  ".join(f"{m}={n}" for m, n in sorted(stats.models_used.items(), key=lambda x: -x[1]))
    if not models_str:
        models_str = "none"

    lines = [
        "",
        "=" * 50,
        "=== Run Summary ===",
        f"  Duration:          {duration_str}",
        f"  Batch fetched:     {stats.batch_size}",
        f"  Backlog remaining: {backlog} pending",
        f"  Pre-filter:        regex={stats.regex_skipped}  llm={stats.llm_skipped}  passed={stats.passed}",
        f"  Scrape:            attempted={stats.scrape_attempted}  success={stats.scrape_success}  failed={stats.scrape_failed}",
        f"  Analysis:          no_content={stats.no_content}  no_insight={stats.no_insight}  llm_error={stats.llm_error}",
        f"  Saved:             {saved_str}",
        f"  Models used:       {models_str}",
    ]

    if last_per_ticker:
        lines.append("  Last analysis per ticker:")
        for ticker, ts in last_per_ticker:
            ts_str = str(ts)[:16] if ts else "never"
            lines.append(f"    {ticker:<6} {ts_str}")

    lines.append("=" * 50)
    print("\n".join(lines), flush=True)


def run_pending_pipeline(limit=20):
    """
    Fetches up to `limit` unanalyzed articles from the DB and runs them
    through the full scrape + analyze + save pipeline.
    """
    stats = RunStats()
    articles = database.get_pending_articles(limit=limit)

    if not articles:
        print("No pending articles found.")
        return []

    stats.batch_size = len(articles)
    print(f"Found {len(articles)} pending articles. Starting pipeline...")
    print("--- Pre-filtering ---")
    articles = _run_pre_filter(articles, stats=stats)
    if not articles:
        print("All articles filtered out. Done.")
        _print_run_summary(stats)
        return []

    run_analysis_pipeline(articles, stats=stats)
    _print_run_summary(stats)
    return articles


def run_analysis_pipeline(articles: list[dict], stats: RunStats = None):
    """
    1. Hydrate (Load existing text)
    2. Scrape (Fetch missing text)
    3. Analyze (LLM Processing)
    4. Save (Persist to DB)
    """
    total = len(articles)

    # --- STAGE 1: HYDRATION & SCRAPING PREP ---
    articles_to_scrape = []

    for article in articles:
        # Check if we already have text in memory or DB
        if article.get('full_text'):
            continue

        # Check DB for content
        existing_text = database.get_article_text(article['id'])
        if existing_text:
            article['full_text'] = existing_text
        else:
            articles_to_scrape.append(article)

    # --- STAGE 2: BATCH SCRAPING ---
    if articles_to_scrape:
        print(f"--- Scraping {len(articles_to_scrape)} articles ---")
        if stats is not None:
            stats.scrape_attempted = len(articles_to_scrape)
        text_map = news_reporter.parallel_fetch_texts(articles_to_scrape)

        for i, article in enumerate(articles_to_scrape):
            full_text = text_map.get(i)

            if full_text:
                article['full_text'] = full_text
                database.mark_scrape_success_and_save(article['id'], full_text)
                if stats is not None:
                    stats.scrape_success += 1
            else:
                database.mark_scrape_failed(article['id'], "Scraper returned empty")
                if stats is not None:
                    stats.scrape_failed += 1

    # --- STAGE 3: ANALYSIS & SAVING ---
    print(f"\n--- Analyzing {total} articles ---")

    for i, article in enumerate(articles):
        headline = article.get('headline', 'Unknown')[:30]
        print(f"[{i+1}/{total}] {headline}...", end="", flush=True)

        # Skip if scrape failed completely
        if not article.get('full_text') and not article.get('api_summary'):
             print(" -> SKIPPING (No content)")
             if stats is not None:
                 stats.no_content += 1
             continue

        # 1. CALL LLM LAYER
        results = analyze_article_impact(article)

        if results is None:
            print(" -> LLM error, skipping (will retry next run).")
            if stats is not None:
                stats.llm_error += 1
            continue

        if not results:
            print(" -> No actionable insights found.")
            database.mark_analysis_done(article['id'])
            if stats is not None:
                stats.no_insight += 1
            continue

        # 2. SAVE RESULTS
        count = 0
        for res in results:
            if not res.success:
                print(f" -> Error: {res.error_msg}")
                continue

            data = res.data

            # Safe extraction using .get()
            saved = database.save_analysis_result(
                article_id=article['id'],
                ticker=res.ticker,
                model=res.model_name,
                prompt_id=res.prompt_id,
                prompt=res.prompt_text,
                impact_headline=data.get('impact_headline'),
                reasoning=data.get('_reasoning') or data.get('reasoning', ''),
                summary=data.get('summary') or data.get('ai_summary', ''),
                relevance=data.get('relevance_score', 0),
                breaking=data.get('breaking_news_score', 0),
                importance=data.get('importance_score', 0),
                full_text=article.get('full_text') # Ensure text is synced
            )
            if saved:
                count += 1
                if stats is not None:
                    stats.saved[res.ticker] = stats.saved.get(res.ticker, 0) + 1
                    stats.models_used[res.model_name] = stats.models_used.get(res.model_name, 0) + 1

        print(f" -> Saved {count} ticker insights.")
        time.sleep(1.5) # Rate limiting

    return articles


# ── Ticker Completion Pipeline ────────────────────────────────────────────────

def _artefact_age_days(ticker: str, artefact_type: str):
    """Returns age of the most recent artefact in days, or None if it doesn't exist."""
    from datetime import datetime, timezone
    artefact = database.get_ticker_artefact(ticker, artefact_type)
    if not artefact or not artefact.get('generated_at'):
        return None
    generated_at = artefact['generated_at']
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - generated_at).total_seconds() / 86400


def refresh_ticker_artefacts(ticker: str, max_age_days: int = 7, force: bool = False):
    """
    Regenerates monthly_news_flow and focal_points for a ticker if stale.

    max_age_days: regenerate if artefact is older than this many days.
    force: skip the age check and always regenerate (used for event-triggered refresh).

    focal_points always regenerates if monthly_news_flow was just regenerated,
    since it reads monthly_news_flow from the DB.
    """
    print(f"[refresh_artefacts] {ticker} (max_age_days={max_age_days}, force={force})")

    mnf_age = _artefact_age_days(ticker, 'monthly_news_flow')
    mnf_stale = force or mnf_age is None or mnf_age > max_age_days
    mnf_regenerated = False

    if mnf_stale:
        reason = "forced" if force else ("missing" if mnf_age is None else f"age={mnf_age:.1f}d")
        print(f"[refresh_artefacts] {ticker} monthly_news_flow regenerating ({reason})")
        try:
            generate_monthly_news_flow(ticker)
            mnf_regenerated = True
        except Exception as e:
            print(f"[refresh_artefacts] {ticker} monthly_news_flow FAILED: {e}")
    else:
        print(f"[refresh_artefacts] {ticker} monthly_news_flow ok (age={mnf_age:.1f}d)")

    fp_age = _artefact_age_days(ticker, 'focal_points')
    fp_stale = force or fp_age is None or fp_age > max_age_days or mnf_regenerated

    if fp_stale:
        reason = "forced" if force else ("missing" if fp_age is None else ("mnf updated" if mnf_regenerated else f"age={fp_age:.1f}d"))
        print(f"[refresh_artefacts] {ticker} focal_points regenerating ({reason})")
        try:
            generate_focal_points(ticker)
        except Exception as e:
            print(f"[refresh_artefacts] {ticker} focal_points FAILED: {e}")
    else:
        print(f"[refresh_artefacts] {ticker} focal_points ok (age={fp_age:.1f}d)")


def run_ticker_completion_pipeline(ticker: str, force: bool = False) -> dict:
    """
    Given a ticker whose summaries rows are complete, generates all artefacts
    needed to make the ticker frontend-ready.

    Steps (skipped if the artefact already exists, unless force=True):
      1. header_description — one-sentence company blurb
      2. monthly_news_flow  — live 2-3 paragraph prose (Grok + search)
      3. focal_points       — strategic themes (depends on monthly_news_flow)
      4. classification     — exchange / sector / display tags (yfinance + LLM)
      5. color              — brand or LLM-generated accent hex

    Returns a dict mapping each step name to 'ok', 'skipped', or 'error: <msg>'.
    """
    import ticker_classification
    import ticker_colors

    # Initialise tables that may not exist yet on fresh DBs
    ticker_colors.init()
    ticker_classification.init()

    results = {}

    def _artefact_exists(artefact_type):
        return not force and database.get_ticker_artefact(ticker, artefact_type) is not None

    # ── 1. Header description ─────────────────────────────────────────────────
    step = "header_description"
    if _artefact_exists(step):
        print(f"[{ticker}] {step}: already exists, skipping")
        results[step] = "skipped"
    else:
        try:
            generate_ticker_header(ticker)
            results[step] = "ok"
            print(f"[{ticker}] {step}: ok")
        except Exception as e:
            print(f"[{ticker}] {step} FAILED: {e}")
            results[step] = f"error: {e}"

    # ── 2. Monthly news flow (run before focal_points — it reads this from DB) ─
    step = "monthly_news_flow"
    if _artefact_exists(step):
        print(f"[{ticker}] {step}: already exists, skipping")
        results[step] = "skipped"
    else:
        try:
            generate_monthly_news_flow(ticker)
            results[step] = "ok"
            print(f"[{ticker}] {step}: ok")
        except Exception as e:
            print(f"[{ticker}] {step} FAILED: {e}")
            results[step] = f"error: {e}"

    # ── 3. Earnings transcripts (no LLM cost — fetched from defeatbeta API) ──
    step = "earnings_transcripts"
    try:
        import ingest_earnings
        ingest_earnings.run(ticker, limit=20, fiscal_year=None, fiscal_quarter=None, commit=True)
        results[step] = "ok"
    except Exception as e:
        print(f"[{ticker}] {step} FAILED: {e}")
        results[step] = f"error: {e}"

    # ── 4. Focal points ───────────────────────────────────────────────────────
    step = "focal_points"
    if _artefact_exists(step):
        print(f"[{ticker}] {step}: already exists, skipping")
        results[step] = "skipped"
    else:
        try:
            generate_focal_points(ticker)
            results[step] = "ok"
            print(f"[{ticker}] {step}: ok")
        except Exception as e:
            print(f"[{ticker}] {step} FAILED: {e}")
            results[step] = f"error: {e}"

    # ── 5. Classification (exchange / sector / display tags) ──────────────────
    step = "classification"
    if not force and database.get_ticker_classification(ticker):
        print(f"[{ticker}] {step}: already exists, skipping")
        results[step] = "skipped"
    else:
        try:
            ticker_classification.get_or_fetch(ticker)
            results[step] = "ok"
            print(f"[{ticker}] {step}: ok")
        except Exception as e:
            print(f"[{ticker}] {step} FAILED: {e}")
            results[step] = f"error: {e}"

    # ── 6. Accent color ───────────────────────────────────────────────────────
    step = "color"
    if not force and database.get_ticker_color(ticker):
        print(f"[{ticker}] {step}: already exists, skipping")
        results[step] = "skipped"
    else:
        try:
            ticker_colors.resolve(ticker)
            results[step] = "ok"
            print(f"[{ticker}] {step}: ok")
        except Exception as e:
            print(f"[{ticker}] {step} FAILED: {e}")
            results[step] = f"error: {e}"

    print(f"\n[{ticker}] Pipeline complete: {results}")
    return results


def run_completion_pipeline_for_all_pending(force: bool = False) -> None:
    """
    Finds every ticker that has summaries but is missing any frontend artefact,
    then runs run_ticker_completion_pipeline for each one.
    """
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT DISTINCT ticker FROM summaries ORDER BY ticker")
    rows = c.fetchall()
    conn.close()

    tickers = [r[0] for r in rows]
    print(f"Found {len(tickers)} tickers with summaries: {', '.join(tickers)}\n")

    for ticker in tickers:
        print(f"\n{'='*60}")
        print(f" Starting completion pipeline for {ticker}")
        print(f"{'='*60}")
        run_ticker_completion_pipeline(ticker, force=force)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the ticker completion pipeline to generate all frontend artefacts."
    )
    parser.add_argument(
        "--ticker",
        help="Single ticker to process (e.g. AMD). Omit to process all tickers with summaries.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate artefacts even if they already exist.",
    )
    args = parser.parse_args()

    if args.ticker:
        run_ticker_completion_pipeline(args.ticker.upper(), force=args.force)
    else:
        run_completion_pipeline_for_all_pending(force=args.force)