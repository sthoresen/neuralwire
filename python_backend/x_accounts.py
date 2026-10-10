"""
x_accounts.py

The curated X accounts the daily job reads. Edit this list freely: every run
syncs it into the x_accounts table. An account removed here is marked
inactive there, and its stored posts are kept.

group  "ai" and "macro" are front-page sections. "wire" accounts are headline
       feeds, read in full and given to the writer as context.

The comments with numbers are from a 6-day counts profile (2026-09-30 to
2026-10-05): original posts per day, excluding replies and reposts.
"""

from typing import NamedTuple


class Account(NamedTuple):
    handle: str
    group: str


ACCOUNTS = [
    # ── AI ───────────────────────────────────────────────────────────────────
    Account("OpenAI", "ai"),
    Account("AnthropicAI", "ai"),
    Account("GoogleDeepMind", "ai"),
    Account("AIatMeta", "ai"),
    Account("MistralAI", "ai"),
    Account("deepseek_ai", "ai"),
    Account("huggingface", "ai"),  # 7.7 posts/day, but only 0.3 originals
    Account("sama", "ai"),
    Account("demishassabis", "ai"),
    Account("karpathy", "ai"),
    Account("jackclarkSF", "ai"),
    Account("emollick", "ai"),  # 4.2/day, the most active
    Account("natolambert", "ai"),
    Account("simonw", "ai"),
    # Compute, chips and the money side of AI (originals/day, 2026-10-03 to 2026-10-08):
    Account("SemiAnalysis_", "ai"),  # 3.5
    Account("dylan522p", "ai"),  # 0.5; most of his posts are replies, which are not fetched
    Account("GavinSBaker", "ai"),  # 0.3, mostly replies
    Account("altcap", "ai"),  # 1.7
    Account("DavidSacks", "ai"),  # 0.3
    Account("naval", "ai"),  # 0.3
    Account("elonmusk", "ai"),  # 17.8, mostly not about AI; ranking and the author cap filter it
    Account("DailyAITechNews", "ai"),  # 4.5, a headline aggregator
    # Account("_akhaliq", "ai"),      # bare paper titles and links
    # Account("swyx", "ai"),          # mostly replies and community chatter
    # Account("ylecun", "ai"),        # 17 posts/day, all replies or reposts
    # Account("AndrewYNg", "ai"),     # no posts at all, replies included: renamed or protected
    # Account("xai", "ai"),           # no posts at all, replies included: renamed or protected
    # Account("Alibaba_Qwen", "ai"),  # no posts at all, replies included: renamed or protected
    #
    # ── Macro ────────────────────────────────────────────────────────────────
    Account("federalreserve", "macro"),
    Account("ecb", "macro"),
    Account("NickTimiraos", "macro"),
    Account("elerianm", "macro"),
    Account("LizAnnSonders", "macro"),  # 12/day, low likes (charts)
    Account("charliebilello", "macro"),
    Account("biancoresearch", "macro"),
    Account("lisaabramowicz1", "macro"),
    Account("Claudia_Sahm", "macro"),
    Account("jasonfurman", "macro"),
    Account("M_McDonough", "macro"),
    Account("Schuldensuehner", "macro"),
    Account("KobeissiLetter", "macro"),  # 11/day, 67% over 1,000 likes
    Account("WSJmarkets", "macro"),
    # Account("TheStalwart", "macro"),  # 11.6/day, the lowest worth in macro: mostly asides
    # Account("markets", "macro"),       # no posts at all, replies included: renamed or protected
    #
    # ── Wire ─────────────────────────────────────────────────────────────────
    Account("DeItaone", "wire"),  # 52/day, the only wire people engage with
    # Account("FirstSquawk", "wire"),  # 279/day, repeats DeItaone's headlines
    # Account("LiveSquawk", "wire"),   # 64/day, no likes at all
    # Account("business", "wire"),     # 227/day, Bloomberg's general feed
]

GROUPS = ("ai", "macro", "wire")
