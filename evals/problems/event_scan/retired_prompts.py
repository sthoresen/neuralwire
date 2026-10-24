"""Prompts production used to run, kept so the eval runs that used them stay reproducible."""

# prompts.event_scan_prompt until October 2026: the scan behind batch_validate
event_scan_v1 = """
You are scanning a financial news analysis to identify if it describes a major new event for {ticker} that belongs on a long-term event timeline.

## Existing events (do not duplicate these):
{existing_events_summary}

## Article to evaluate:
Published:      {published_at}
Headline:       {headline}
Impact:         {impact_headline}
Summary:        {ai_summary}

## Task
Decide if this article describes a NEW major event not already captured above.

The GOLDEN rule: Compare the candidate events with the existing, vetted event_list. Would the event have been accepted in the past?
   If there are few events (< 3 per quarter), you can add events more freely.

Qualifies as a major event:
- Product launches or architecture announcements
- Acquisitions, mergers, divestitures
- Earnings surprises (significant beats/misses, not routine coverage)
- Regulatory actions, export controls, lawsuits
- Leadership changes
- Major partnerships or customer wins
- Macro or geopolitical events with direct company impact

Does NOT qualify:
- Routine analyst price target changes or upgrades/downgrades
- Repetitive earnings previews or follow-up commentary
- General market moves where the company is incidentally mentioned
- Events clearly already listed above

Respond with JSON only — no other text:
{{"is_event": false}}
OR
{{"is_event": true, "title": "Short factual title (max 10 words)", "event_date": "YYYY-MM-DD", "event_date_label": "Human date label", "description": "Exactly one sentence."}}
"""

# prompts.event_validate_prompt until October 2026
event_validate_v1 = """
You are curating a long-term event timeline for {ticker}. A cheap model has scanned recent news and produced candidate events. Your job is to decide which candidates are worth adding.

## Current event list:
{existing_events}

## Candidates to evaluate:
{candidates_json}

## Rules

The GOLDEN rule: Compare the candidate events with the existing, vetted event_list. Would the event have been accepted in the past?
   If there are few events (< 3 per quarter), you can add events more freely.

1. Discard candidates that duplicate or closely overlap with an existing event.
2. Discard candidates that are not genuinely significant (routine analysis, minor price moves, repetitive commentary).
3. Merge candidates that describe the same underlying event into one row; union their source_article_ids.
4. For approved candidates: improve the title and description wording if needed. Descriptions must be exactly one sentence.
5. Preserve event_date and event_date_label from the candidate unless clearly wrong.

Return a JSON array of approved events — no other text:
[{{"title": "...", "event_date": "YYYY-MM-DD", "event_date_label": "...", "description": "...", "source_article_ids": [...]}}]

If no candidates merit inclusion, return: []
"""
