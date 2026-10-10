"""
x_digest.py

Writes the front page's briefings from stored, Jev-scored X posts, per section:
  daily   the 24 hours before the refresh, ranked by x_jev.rank_score: a title, a
          lead paragraph and a few one-line "Also" stories
  weekly  the 7 days before the refresh: a title and three short stories, from those
          days' top posts with their daily briefings as context

The LLM sees each post under a short reference ([3]) and cites those inline. The
code rewrites each citation to [[post_id]] and drops any it can't map, so every
citation on the page links to a real post.
"""

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import llms
import prompts
import x_jev
import x_store

DAILY_PROMPT_ID = "x_daily_digest_v5"
WEEKLY_PROMPT_ID = "x_weekly_digest_v5"

SECTIONS = {
    "ai": {
        "title": "daily AI briefing",
        "weekly_title": "AI briefing",
        "group": "ai",
        "examples": (prompts.x_daily_example_ai, prompts.x_weekly_example_ai),
        "candidates": (15, 25),  # daily, weekly
        "per_author": (3, 4),
    },
    "macro": {
        "title": "daily market-backdrop briefing",
        "weekly_title": "market-backdrop briefing",
        "group": "macro",
        "examples": (prompts.x_daily_example_macro, prompts.x_weekly_example_macro),
        "candidates": (12, 20),
        "per_author": (2, 3),
        "wire": (15, 20),  # top wire headlines, added to the posts as citable context
    },
}
WIRE_NOTE = "The last ones are short headlines from a news wire."
QUOTE_CHARS = 300  # a quoted post that isn't in the list itself is cut to this length

CITATION = re.compile(r"\[(\d+)\]")


ET = ZoneInfo("America/New_York")
DAY = timedelta(hours=24)
WEEK = timedelta(days=7)


def et_label(t: datetime) -> str:
    """'Tuesday, October 6, 9:35 a.m. ET': how the prompts and the page name a moment."""
    t = t.astimezone(ET)
    clock = t.strftime("%-I:%M %p").replace("AM", "a.m.").replace("PM", "p.m.")
    return f"{t:%A, %B} {t.day}, {clock} ET"


# ── Picking posts ────────────────────────────────────────────────────────────


def _has_content(post: dict) -> bool:
    text = x_jev.readable_text(post.get("text"), post.get("links"), post.get("media"))
    return len(text) >= 15 or bool(post.get("quoted"))


def select(posts: list[dict], group: str, limit: int, per_author: int) -> list[dict]:
    """
    The highest-ranked posts of a group from accounts still on the list, at most `per_author`
    from any one account.
    """
    ranked = sorted(
        (
            p
            for p in posts
            if p["group_name"] == group and p.get("active") and p.get("scores") and _has_content(p)
        ),
        key=lambda p: x_jev.rank_score(p["scores"], group),
        reverse=True,
    )
    picked, per = [], {}
    for p in ranked:
        h = p["author_handle"].lower()
        if per.get(h, 0) < per_author:
            picked.append(p)
            per[h] = per.get(h, 0) + 1
        if len(picked) == limit:
            break
    return picked


def showcase(posts: list[dict], per_group: dict[str, int]) -> list[str]:
    """Post ids for the "Top posts" strip: the highest-ranked post per account, per group."""
    ids = []
    for group, n in per_group.items():
        ids += [p["post_id"] for p in select(posts, group, n, per_author=1)]
    return ids


# ── Prompt and reply ─────────────────────────────────────────────────────────


def format_post(ref: int, post: dict, refs_by_id: dict[str, int], with_date: bool = False) -> str:
    """
    One post as the writer sees it. A quoted post that is itself in the list is referred to by
    its reference instead of repeated; any other quoted post is shortened to QUOTE_CHARS.
    """
    likes = post.get("likes") or 0
    when = f", {post['created_at']:%a %b} {post['created_at'].day}" if with_date else ""
    name = post.get("author_name")
    who = f"@{post['author_handle']}" + (f" ({name})" if name else "")
    text = x_jev.readable_text(post.get("text"), post.get("links"), post.get("media"))
    lines = [f"[{ref}] {who}{when}, {likes:,} likes", text]
    if post.get("media"):
        lines.append(f"(attached: {x_jev.describe_media(post['media'])})")
    q = post.get("quoted")
    if q and q.get("post_id") in refs_by_id:
        lines.append(f"(quoting [{refs_by_id[q['post_id']]}])")
    elif q:
        quoted = x_jev.readable_text(q.get("text"), q.get("links"), q.get("media"))
        if len(quoted) > QUOTE_CHARS:
            quoted = quoted[:QUOTE_CHARS].rsplit(" ", 1)[0] + "..."
        lines.append(f"(quoting @{q.get('author')}: {quoted})")
    return "\n".join(lines)


def _post_blocks(candidates: list[dict], wire: list[dict], with_date: bool) -> tuple[str, dict]:
    """The prompt's post list, and the map from each reference to its post id."""
    posts = candidates + wire
    refs = {i: p["post_id"] for i, p in enumerate(posts, start=1)}
    refs_by_id = {pid: i for i, pid in refs.items()}
    blocks = [format_post(i, p, refs_by_id, with_date) for i, p in enumerate(candidates, start=1)]
    if wire:
        blocks.append("Headlines from the @DeItaone news wire, also citable:")
        start = len(candidates) + 1
        blocks += [
            format_post(i, p, refs_by_id, with_date) for i, p in enumerate(wire, start=start)
        ]
    return "\n\n".join(blocks), refs


class _Citations:
    """Rewrites [3] to [[post_id]] and remembers the order posts were first cited in."""

    def __init__(self, refs: dict[int, str]):
        self.refs, self.cited = refs, []

    def __call__(self, text: str) -> str:
        def rewrite(m: re.Match) -> str:
            pid = self.refs.get(int(m.group(1)))
            if pid is None:
                return ""
            if pid not in self.cited:
                self.cited.append(pid)
            return f"[[{pid}]]"

        text = CITATION.sub(rewrite, " ".join(text.split()))
        return re.sub(r"\s+([.,;:])", r"\1", text)  # a dropped citation can leave "word ."


def _blocks(reply: str | None) -> tuple[str, list[str]] | None:
    """(title, the blank-line separated blocks after it), with code fences and labels removed."""
    text = re.sub(r"```\w*", "", reply or "").strip()
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if len(blocks) < 2:
        return None
    title = re.sub(r"^(#+\s*|title:\s*)", "", blocks[0], flags=re.IGNORECASE).strip(' *"')
    return title, blocks[1:]


def parse_daily(reply: str | None, refs: dict[int, str]) -> dict | None:
    """
    A daily reply (title, lead paragraph, "Also:" with "- " lines) as page content:
    {"title", "paragraphs", "also", "post_ids"}. None if it has no lead.
    """
    parsed = _blocks(reply)
    if not parsed:
        return None
    title, blocks = parsed
    cite = _Citations(refs)
    paragraphs, also = [], []
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if re.match(r"^also:?$", lines[0], flags=re.IGNORECASE):
            lines = lines[1:]
        bullets = [ln for ln in lines if ln.startswith(("- ", "* ", "• "))]
        if bullets:
            also += [cite(ln[2:]) for ln in bullets]
        elif lines:
            paragraphs.append(cite(" ".join(lines)))
    if not paragraphs:
        return None
    return {"title": title, "paragraphs": paragraphs, "also": also, "post_ids": cite.cited}


def parse_weekly(reply: str | None, refs: dict[int, str]) -> dict | None:
    """
    A weekly reply (title, then stories as a heading line plus text) as page content:
    {"title", "stories": [{"heading", "text"}], "post_ids"}. None if it has no stories.
    """
    parsed = _blocks(reply)
    if not parsed:
        return None
    title, blocks = parsed
    cite = _Citations(refs)
    stories = []
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) >= 2:
            heading = lines[0].strip(" *#:.")
            stories.append({"heading": heading, "text": cite(" ".join(lines[1:]))})
    if not stories:
        return None
    return {"title": title, "stories": stories, "post_ids": cite.cited}


def plain_text(content: dict) -> str:
    """A briefing's text without citations, to give a later prompt as context."""
    parts = content.get("paragraphs", []) + content.get("also", [])
    parts += [f"{s['heading']}: {s['text']}" for s in content.get("stories", [])]
    return " ".join(strip_citations(p) for p in parts)


def strip_citations(text: str) -> str:
    return re.sub(r"\s*\[\[\d+\]\]", "", text)


def _write(
    prompt: str, parse, refs: dict[int, str], what: str, model_id: str | None = None
) -> tuple[dict, str | None]:
    """
    Call the LLM and parse its briefing, with one retry if the reply is unusable.
    model_id picks one model (e.g. to compare writers); None routes by tier.
    """
    manager = llms.LLMProviderManager()
    model = None
    for _ in range(2):
        if model_id:
            reply, model = manager.call_model(model_id, prompt, reasoning=False, max_tokens=2000)
        else:
            reply, model = manager.call(prompt, tier="standard", reasoning=False, max_tokens=2000)
        content = parse(reply, refs)
        if content and content["post_ids"]:
            return content, model
    raise RuntimeError(f"[x_digest] no usable briefing for {what} ({model})")


# ── Writing ──────────────────────────────────────────────────────────────────


def _digest(section, period, end, length, content, candidates, model, prompt_id, prompt) -> dict:
    return {
        "edition_at": end,
        "window_start": end - length,
        "window_end": end,
        "section": section,
        "period": period,
        "content": content,
        "candidate_ids": [int(p["post_id"]) for p in candidates],
        "model_name": model,
        "prompt_id": prompt_id,
        "prompt": prompt,
    }


def daily_candidates(section: str, end: datetime) -> tuple[list[dict], list[dict]]:
    """The posts the daily briefing would be written from: (section posts, wire headlines)."""
    cfg = SECTIONS[section]
    posts = x_store.posts_between(end - DAY, end, x_jev.QSET)
    candidates = select(posts, cfg["group"], cfg["candidates"][0], cfg["per_author"][0])
    wire = select(posts, "wire", cfg["wire"][0], cfg["wire"][0]) if "wire" in cfg else []
    return candidates, wire


def write_daily(
    section: str, end: datetime, model_id: str | None = None, candidates=None
) -> dict | None:
    """
    The briefing for `section` on the 24 hours before `end`. `candidates` can pass in what
    daily_candidates returned, so the caller can first check whether anything changed.
    None if there are no posts.
    """
    cfg = SECTIONS[section]
    posts, wire = candidates or daily_candidates(section, end)
    if not posts and not wire:
        return None
    block, refs = _post_blocks(posts, wire, with_date=False)
    prompt = prompts.x_daily_digest_prompt.format(
        section_title=cfg["title"],
        period=f"the 24 hours to {et_label(end)}",
        context_note=WIRE_NOTE if wire else "",
        example=cfg["examples"][0],
        posts=block,
    )
    content, model = _write(
        prompt, parse_daily, refs, f"{section} to {end:%Y-%m-%d %H:%M}", model_id
    )
    return _digest(section, "day", end, DAY, content, posts + wire, model, DAILY_PROMPT_ID, prompt)


def write_weekly(section: str, end: datetime, model_id: str | None = None) -> dict | None:
    """The briefing for `section` on the 7 days before `end`. None if there are no posts."""
    cfg = SECTIONS[section]
    start = end - WEEK
    posts = x_store.posts_between(start, end, x_jev.QSET)
    candidates = select(posts, cfg["group"], cfg["candidates"][1], cfg["per_author"][1])
    wire = select(posts, "wire", cfg["wire"][1], cfg["wire"][1]) if "wire" in cfg else []
    if not candidates and not wire:
        return None

    dailies = x_store.daily_digests_between(start, end, section)
    context = "\n\n".join(
        f"{et_label(d['edition_at'])}: {d['content']['title']}\n" + plain_text(d["content"])
        for d in dailies
    )
    block, refs = _post_blocks(candidates, wire, with_date=True)
    prompt = prompts.x_weekly_digest_prompt.format(
        section_title=cfg["weekly_title"],
        period=f"the 7 days to {et_label(end)}",
        context_note=WIRE_NOTE if wire else "",
        example=cfg["examples"][1],
        dailies=context or "(none written)",
        posts=block,
    )
    content, model = _write(
        prompt, parse_weekly, refs, f"weekly {section} to {end:%Y-%m-%d}", model_id
    )
    return _digest(
        section, "week", end, WEEK, content, candidates + wire, model, WEEKLY_PROMPT_ID, prompt
    )
