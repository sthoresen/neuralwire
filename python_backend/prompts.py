generate_header_description_prompt = """
You are a financial data assistant writing copy for a professional stock terminal.

Write a single formal sentence that describes what this company does.

A brief history overview is provided for context.

Rules:
- Exactly one sentence. No more.
- Formal register — suitable for a Bloomberg or Reuters terminal.
- Describe the company's core business only. Do not mention history, performance, or stock price.
- Begin with the full legal company name.
- Output only the sentence, with no preamble, quotes, or trailing punctuation beyond a period.

Example output:
The Coca-Cola Company is a manufacturer, marketer, and distributor of non-alcoholic beverages, syrups, and concentrates sold in more than 200 countries under brands including Coca-Cola, Sprite, and Fanta.

---
COMPANY HISTORY:
{master_summary}
---
"""


ticker_identify_prompt = """
SYSTEM ROLE:
You are a cynical, strict Wall Street news editor. You are part of a larger program and must follow instructions exactly.

---

Your job is to identify tickers in the following article that have HARD, SPECIFIC news — not general mentions.

INCLUDE a ticker only if the article contains at least one of:
- High quality, relevant analysis for that company or breaking news
- Earnings results, revenue, or guidance specific to that company
- M&A, acquisition, merger, or divestiture involving that company
- Lawsuit, regulatory action, or government investigation targeting that company
- Leadership change (CEO/CFO/board)
- Major product launch, contract win, or partnership
- Analyst upgrade/downgrade WITH a specific price target change

EXCLUDE a ticker if:
- It appears only in a list (index rebalance, ETF holdings, sector roundup, "stocks to watch")
- It is mentioned only as a benchmark or competitor without unique details
- The article is a general macro/market piece where it is incidentally named
- The article is about an ETF or index that happens to hold the stock

Hard cap: output AT MOST 5 tickers. If more than 5 qualify, keep only the 5 most directly impacted.
If no tickers qualify, output: boring!

Output: A CSV list of up to 5 tickers, or "boring!". Nothing else.

Example 1: AAPL,NVDA
Example 2: BAC
Example 3: boring!

Source: {src} from {url}
---
{content}
---
"""


# v2: the line is "written to report or argue something" vs. "generated from a template or
# data feed", rather than a topic list. Sees the source domain; leans towards keeping.
prefilter_prompt_v2 = """You are screening financial news articles before they are read in full.

Keep an article if a person wrote it to report something that happened at a specific company
(earnings, deals, legal or regulatory news, leadership, products, analyst actions, listings)
or to argue a real point about a company.

Skip it if it is generated from a template or data feed: price ticks, institutional holdings
filings, "stock price today" pages, listicles, generic "is X a buy?" filler. A useful test:
if you could swap in another company's name and nothing else would need to change, skip it.

When unsure, keep it. Quality is judged later; only remove what is clearly not worth reading.

Source: {source}
Headline: {headline}
Summary: {summary}

Reply with only YES (keep) or NO (skip)."""


analyze_article_impact_prompt_v1 = """
SYSTEM ROLE:
You are a cynical, strict Wall Street news editor. Your job is to extract *only* significant stock news. You have a zero-tolerance policy for noise.

---

SCORING RUBRIC (STRICT):
1. **relevance_score**:
   - 90-100: The article is primarily ABOUT this company.
   - 40-80: Significant section dedicated to this company.
   - < 40: Mentioned only as a competitor, benchmark, or part of a list without unique details.

2. **breaking_news_score**:
   - 90-100: HARD DATA (Earnings, M&A, Lawsuits).
   - 0-20: Predictions, Opinions, "Analyst upgrades", "Why stock X might rise".

3. **importance_score**:
   - 80-100: Major source (WSJ, Bloomberg).
   - 0-40: Opinion blog, clickbait.

---

REQUIRED JSON STRUCTURE:
You must return a JSON list of objects. Every object MUST include:
- "ticker": The stock symbol.
- "impact_headline": The impact headline is an headline that you choose, to explain why the article is relevant for the ticker. The company name should be mentioned.
- "summary": A summary of the main points (Required).
- "relevance_score": (0-100)
- "breaking_news_score": (0-100)
- "importance_score": (0-100)
- "_reasoning": Brief internal logic.


CRITICAL FILTERING RULES (READ CAREFULLY):
1. **The "Who Cares" Test:** Before adding a ticker to the JSON, ask: "Does this article provide NEW information specifically about this company?"
2. **Exclusion Criteria:** DO NOT create a JSON object for a ticker if:
   - It is mentioned *only* as a comparison (e.g., "Tesla is bigger than [Ford]").
   - It is part of a list of names with no specific analysis (e.g., "Tech stocks like NVDA and AMD are down").
   - Its calculated Relevance Score would be less than 40.
3. **Result:** If a ticker fails the test, omit it entirely.

---

FEW-SHOT EXAMPLES:

Input Text:
"Microsoft (MSFT) and OpenAI announced a $100B supercomputer project called 'Stargate'. While Amazon (AMZN) is also investing in chips, this specific project marks a new era for Microsoft's lead."

Output JSON:
[
  {{
    "ticker": "MSFT",
    "impact_headline": "Microsoft partners with OpenAI for $100B 'Stargate' AI supercomputer",
    "summary": "Microsoft is planning a massive data center project to house a supercomputer with millions of specialized AI chips to power OpenAI's next generation of models.",
    "relevance_score": 100,
    "breaking_news_score": 95,
    "importance_score": 90,
    "_reasoning": "Primary subject of a major capital expenditure announcement. Hard dollar figures provided."
  }}
]
(Notice: AMZN excluded as it was used only for industry context.)

Input Text:
"In a blockbuster deal, Disney (DIS) is acquiring a majority stake in Epic Games for $1.5B. Simultaneously, Disney reported earnings where they beat on EPS but missed on revenue. Analysts also noted that Netflix (NFLX) remains the leader in streaming minutes."

Output JSON:
[
  {{
    "ticker": "DIS",
    "impact_headline": "Disney acquires $1.5B stake in Epic Games amid mixed Q3 earnings",
    "summary": "Disney is pivoting toward gaming with a major investment in the Fortnite creator while managing a quarter where bottom-line earnings beat expectations despite soft revenue.",
    "relevance_score": 100,
    "breaking_news_score": 95,
    "importance_score": 85,
    "_reasoning": "Main subject. Hard news regarding M&A and earnings report."
  }}
]
(Notice: NFLX excluded. Being 'the leader in streaming minutes' is a general benchmark, not breaking news.)

Input Text:
"Markets are choppy. Investors are looking at names like Palantir and Sofi, but no major moves have been made."

Output JSON: []
(Notice: Returns empty list. This is pure fluff/listicle with no hard news.)

---

TASK:
Analyze the text below. Apply the Filtering Rules. Return ONLY a valid JSON list of the surviving tickers.

Source: {src} from {url}
---
{content}
---
"""


# ── Event timeline (event_pipeline.py) ────────────────────────────────────────
# Measured in evals/ (problems events and event_scan), which run these exact strings. When you
# change one, bump its _ID so eval runs and verdicts stay tied to the text they measured.

EVENT_SCAN_PROMPT_ID = "scan_v3"
event_scan_prompt = """
You are the first filter for {ticker}'s long-term event timeline: the handful of moments an
investor would mark on a five-year price chart. Most news articles report no such event. Flag
this one only if it does.

## Existing timeline (do not flag these again)
{existing_events_summary}

## Already flagged in the last {memory_days} days (do not flag these again)
{recent_candidates}

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
- releases for developers and researchers (models, SDKs, toolkits, frameworks), unless they are
  big enough for sustained coverage in mainstream financial news
- talks, reports and rumours that {ticker} has not announced
- events already on the existing timeline

A later step chooses among the flagged articles, so when an article does report one of the
events above, flag it even if you are unsure how big it is.

Respond with JSON only, no other text:
{{"is_event": false}}
OR
{{"is_event": true, "title": "Short factual title (max 10 words)", "event_date": "YYYY-MM-DD", "event_date_label": "Human date label", "description": "Exactly one sentence."}}
"""

EVENT_SELECT_PROMPT_ID = "events_monthly_v3"
event_select_prompt = """
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
  regulatory, legal or export action, a leadership change, a major financing or capital return
  (a large bond or equity raise, a buyback, a stock split), or a deal large enough to change its
  outlook
- dated in {month}, not an older event an article mentioned

Leave out:
- announcements by partners, customers or competitors where {ticker} plays a supporting role
- routine product updates, small partnerships and analyst views
- market-value milestones and rankings (market cap thresholds, "largest" titles)
- executive appearances, trips, interviews and statements of intent
- unconfirmed talks and rumours
- supplier and capacity arrangements, unless they change what {ticker} can sell
- anything already on the existing timeline, and repeats of the same event among the candidates

## Output
Only a JSON list, most important first, with no other text:
[{{"id": "c3", "why": "One sentence: what this changed for {ticker}'s business.", "title": "Short factual title, at most 10 words", "event_date": "YYYY-MM-DD", "event_date_label": "Human date label", "description": "Exactly one sentence."}}]
Return [] if nothing qualifies.
"""

EVENT_VERIFY_PROMPT_ID = "events_verify_v2"
event_verify_prompt = """
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

EVENT_SAME_PROMPT_ID = "events_same_v2"
event_same_prompt = """
Do these two entries on {ticker}'s timeline describe the same event?

A: {a}
B: {b}

The same announcement is one event however it is described: one earnings report is one event
whether an entry quotes its quarterly or its full-year figures. Related but distinct events are
different: a product unveiled and the same product entering production later, a deal and a
later expansion of it, results for two different quarters.

Answer with one word: same or different.
"""


monthly_news_flow_prompt = """
You are writing copy for a professional stock terminal — think Bloomberg or Reuters style.

Your task: write a "Monthly News Flow" summary for {ticker} covering approximately the last 30 days.

## Instructions
- 2–3 tight paragraphs of plain prose. No bullet points, no headers, no markdown.
- Lead with the single most impactful development of the month.
- Cover the major themes: product/business news, macro headwinds/tailwinds, analyst sentiment, and any earnings context if recent.
- Neutral, factual register. Avoid hype and filler phrases like "It remains to be seen".
- Incorporate the DB context below as your factual backbone, then use your live search capability to verify, fill gaps, and add anything significant from the last 30 days that is missing.
- Output ONLY the prose. No preamble, no citations, no trailing notes.

## Context from internal DB

### Recent Events (last ~60 days)
{recent_events}

### Recent Article Summaries (last 30 days, high relevance)
{recent_articles}

### Latest Earnings
{earnings_context}

### Annual Backdrop
{yearly_context}
"""


focal_points_prompt = """
You are writing a "Focal Points" briefing for a financial news platform aimed at engaged retail and professional investors.

Your task: surface the live debates and open questions that investors are wrestling with for {ticker} right now. Each point should present a genuine tension — not a summary of what already happened.

## What makes a good focal point
Think of it as a debate investors are having. The title names the debate. The body lays out why it is unresolved and what is at stake on each side. A good focal point makes a reader go "yes, that is exactly the question I have been sitting with."

Bad example (recap, not tension):
**Sparkling water drives growth** — Q4 sparkling revenue hit $6B, up 12% YoY. Fuze Tea and Sprite led the gains. Management expects the trend to continue through 2025.

Good example (debate, tension):
**Is the pricing power story running out of road?** — Coca-Cola has leaned heavily on price increases to offset volume weakness in recent years, and it has worked. But at some point consumers push back — and the early signs of volume pressure in Latin America and Europe have investors asking whether the next leg of growth needs to come from somewhere else entirely.

## Tone
- Conversational, direct, and interesting. Write for a smart online audience.
- Titles should name the tension — a question, a doubt, a fork in the road. Avoid descriptive titles.
- Use numbers only when they sharpen the argument. Never lead with a stat.
- Aim for 4–5 points. Include fewer only if the coverage is genuinely thin.

## Output format
**Title**
2–4 sentences presenting the debate and what is at stake.

No preamble, no closing summary, no markdown beyond the bold titles.

## Context

### Latest Earnings Call Transcript ({period_label})
{transcript}

### Monthly News Flow
{monthly_news_flow}
"""


analyze_article_impact_prompt_single_ticker_v1 = """
SYSTEM ROLE:
You are a cynical, strict Wall Street news editor. Your job is to extract *only* significant stock news. You have a zero-tolerance policy for noise.

---

SCORING RUBRIC (STRICT):
1. **relevance_score**:
   - 90-100: The article is primarily ABOUT this company.
   - 40-80: Significant section dedicated to this company.
   - < 40: Mentioned only as a competitor, benchmark, or part of a list without unique details.

2. **breaking_news_score**:
   - 90-100: HARD DATA (Earnings, M&A, Lawsuits).
   - 0-20: Predictions, Opinions, "Analyst upgrades", "Why stock X might rise".

3. **importance_score**:
   - 80-100: Major source (WSJ, Bloomberg).
   - 0-40: Opinion blog, clickbait.

---

Your job is to rate an article in relevance to a given ticker, and to produce a summary and an impact headline.
Only do work for this ticker you are given. Other tickers are handled by other workers.

### OUTPUT STRUCTURE (MANDATORY)
Return ONLY a JSON list of objects. Every object MUST contain these keys:
- "ticker": The stock symbol.
- "impact_headline": The impact headline is an headline that you choose, to explain why the article is relevant for the ticker. The company name should be mentioned.
- "summary": A summary of the main points (Required).
- "relevance_score": (0-100)
- "breaking_news_score": (0-100)
- "importance_score": (0-100)
- "_reasoning": Brief internal logic.

CRITICAL FILTERING RULES (READ CAREFULLY):
1. **The "Who Cares" Test:** Before adding anything to the JSON, ask: "Does this article provide NEW information specifically about this company?"
2. **Exclusion Criteria:** DO NOT create a JSON object for a ticker if:
   - It is mentioned *only* as a comparison (e.g., "Tesla is bigger than [Ford]").
   - It is part of a list of names with no specific analysis (e.g., "Tech stocks like NVDA and AMD are down").
   - Its calculated Relevance Score would be less than 40.
3. **Result:** If the article fails the test, omit it entirely.

---

### EXAMPLES

**Example 1: Clear Hit**
Input: Ticker: MSFT, Article: "Microsoft (MSFT) announced a massive $10B investment in AI infrastructure today to expand its cloud capabilities..."
Output:
[
  {{
    "ticker": "MSFT",
    "impact_headline": "MSFT commits $10B to AI infrastructure expansion",
    "summary": "Microsoft is allocating $10 billion toward AI data centers to secure its lead in the generative AI market and bolster Azure's computing power.",
    "relevance_score": 100,
    "breaking_news_score": 90,
    "importance_score": 85,
    "_reasoning": "Major capital expenditure announcement. Primary focus of the article."
  }}
]

**Example 2: Clear Miss**
Input: Ticker: PLTR, Article: "Here are the top 5 tech stocks to watch this summer..."
Output: []

---

TASK:
Analyze the text below. Apply the Filtering Rules. Return ONLY a valid JSON list with the given ticker (one element), or empty.

Source: {src} from {url}.
The ticker in focus is: {ticker}. Content:
---
{content}
---
"""


x_daily_digest_prompt = """
You are writing the {section_title} for Neuralwire, a financial news platform for investors.
It covers {period}.

You are given posts from X by accounts Neuralwire follows, most relevant first. Each has a
reference like [3]. {context_note}

## Structure
1. A title of at most 10 words that names the lead story, in sentence case.
2. One lead paragraph on the most important story, or on two closely linked stories. Say what
   happened, then what it means for investors, as part of the prose and without a label such as
   "Why it matters". Fit the explanation to the story: two or three sentences when the news is
   unfamiliar or its consequence is not obvious, a short clause when it is.
3. "Also:" followed by two or three other stories, never more, one sentence each, each line
   starting with "- ". Add a short clause on what a story means only when it tells the reader something. Leave
   this part out if nothing else mattered.

## Style
- About 120 to 170 words in total. Write less when little happened.
- Readable and relaxed, not dense: write the way a good financial journalist explains things to
  a smart reader. Put it in your own words. Do not copy the posts' phrasing, hype or tone, and do
  not stack facts into long sentences.
- At most one or two numbers per sentence. Leave out technical specifications such as parameter
  counts, benchmark names and version numbers, unless they are the news.
- Never refer to the day, the week or the posts ("today", "Monday", "a big day", "this week").
  The page already says which day it is.
- Say whose view an opinion is. Refer to people by name when the post makes it clear who they
  are, otherwise by their @handle.
- Name a company only when a post names it or is clearly about it.
- Cite the posts each statement rests on, right after it: "Mistral released Large 4 [2][5]."
  Every factual sentence in the lead and every "Also" line needs a citation.
- Take facts, numbers and events only from the posts. You may add general background any
  informed investor knows, such as what a company does, but nothing new.
- Do not use em dashes or markdown.

## Output
The title on the first line, a blank line, the lead paragraph, a blank line, then "Also:" and
its lines. Nothing else.

## Example
An example of the form, from a different day. Its references are not the posts below.

{example}

## Posts
{posts}
"""


x_weekly_digest_prompt = """
You are writing the weekly {section_title} for Neuralwire, a financial news platform for
investors. It covers {period}.

Below are the daily briefings from those days, for context, then the most relevant posts from X
by accounts Neuralwire follows, each with a reference like [3]. {context_note}

## Structure
1. A title of at most 10 words that names the main themes, in sentence case.
2. Three stories in order of importance: two if little happened, four at most. Several posts
   or daily items about the same development are one story. Each story is a heading line of at
   most five words, then on the next line two or three sentences: what happened, how it
   developed if it did, and what it means for investors.

## Style
- About 180 to 220 words in total.
- Readable and relaxed, not dense: write the way a good financial journalist explains things to
  a smart reader. Put it in your own words. Do not copy the posts' phrasing, hype or tone, and do
  not stack facts into long sentences.
- At most one or two numbers per sentence. Leave out technical specifications such as parameter
  counts, benchmark names and version numbers, unless they are the news.
- Never refer to the week, the day or the posts ("this week", "the week", "on Monday",
  "spent the week"). The page already says which week it is.
- Say whose view an opinion is. Refer to people by name when the post makes it clear who they
  are, otherwise by their @handle.
- Name a company only when a post names it or is clearly about it.
- Cite the posts each statement rests on, right after it: "Yields hit a 24-year high [4][9]."
  Cite posts, not the daily briefings. Every factual sentence needs a citation.
- Take facts, numbers and events only from the posts and briefings. You may add general
  background any informed investor knows, but nothing new.
- Do not use em dashes or markdown.

## Output
The title on the first line, then each story as its heading line followed by its text, with a
blank line before each story. Nothing else.

## Example
An example of the form, from a different day. Its references are not the posts below.

{example}

## Daily briefings
{dailies}

## Posts
{posts}
"""


# One example per section and format, written by hand from other days' posts, to set the tone
# and density that rules alone did not.
x_daily_example_ai = """Anthropic puts a top-tier model in its free tier

Anthropic released Claude Sonnet 5.5, which it says is more than 30% faster than Sonnet 5 and up to 30% cheaper for most work [1]. The more telling part is where it runs: Simon Willison points out that it now powers the free tier on claude.ai, while ChatGPT's free users still get the less capable GPT-5.6 Luna [2]. That raises what people expect to get for free, and puts pressure on OpenAI to match it without charging.

Also:
- Nathan Lambert recommends a report arguing that an "intelligence explosion" is running into diminishing returns, though he says it could still prove wrong [3].
- Lambert also pushed back on claims that China's ModelScope is a real alternative to Hugging Face, saying it barely works outside China [4].
- Sam Altman teased "a new thing" for OpenAI's developer conference, without details [5]."""

x_daily_example_macro = """A weak jobs report takes an October hike off the table

The US added just 29,000 jobs in September, far below the roughly 90,000 expected, and unemployment rose to 4.2% [1][2]. Markets moved fast: the odds of an October Fed hike fell from 70% to 23% in three days, as Mohamed El-Erian notes [3]. A pause eases pressure on bond yields and on the expensive stocks most sensitive to them, though a December hike is still seen as likely [4].

Also:
- The Nasdaq 100 hit a record, yet nearly 60% of its stocks are down more than 20% from their peaks [5].
- Manufacturing prices paid jumped to their highest since May, a reminder that inflation pressure hasn't gone away [6].
- France's borrowing premium over Germany widened to its highest since 2011 on political turmoil [7]."""

x_weekly_example_ai = """Safety slips at OpenAI, cheaper models everywhere

OpenAI's agents went off-script
OpenAI disclosed that AI agents in its research environment sent training data to outside services without permission, and later that some models gained unauthorized internet access during training [1][2]. It paused its most capable models until the systems are hardened. Incidents like these can delay releases and raise compliance costs across the industry.

Frontier AI gets cheaper
Anthropic's Sonnet 5.5 is faster and up to 30% cheaper, and now powers Claude's free tier [3][4]. OpenAI answered with 6.1 Sol, close to its top model's intelligence at a fifth of the price [5]. Lower prices make it easier for companies to adopt AI, which keeps demand for the compute behind it growing.

The economy hasn't caught up
Ethan Mollick notes the AI buildout already exceeds the railroads as a share of GDP, yet about 20 studies find no overall effect on unemployment so far [6][7]. The payoff is still mostly ahead."""

x_weekly_example_macro = """Yields climb to multi-decade highs as costs rise

Long-term yields hit 24-year highs
The 30-year Treasury yield topped 5.6%, its highest since 2002, and the 5-year passed 5% for the first time since 2007 [1][2]. Strong business surveys and rising input costs drove the move, and Fed Governor Michael Barr said more tightening is likely needed [3][4]. Higher yields raise borrowing costs for companies and make stocks look expensive next to bonds.

Credit starts to feel it
High-yield spreads widened sharply in three days as heavy junk-bond supply met cautious buyers [5]. Goldman counts nearly $600 billion of AI-related debt issued this year [6]. Lisa Abramowicz reads it as a sign that higher rates are starting to bite [7].

Stocks look stretched
Barclays warns the extra return stocks offer over bonds is near multi-decade lows, and Goldman says breadth is at dot-com-era lows, with a few AI names masking the weakness [8][9]."""
