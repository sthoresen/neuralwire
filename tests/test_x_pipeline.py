"""X pipeline: query building, Apify item parsing, the budget stop and the rebuild guard."""

from datetime import UTC, datetime, timedelta

import pytest

import run_x_refresh
import x_digest
import x_jev
import x_sources
import x_store

START = datetime(2026, 10, 5, tzinfo=UTC)
END = START + timedelta(days=1)


def apify_item(**overrides):
    item = {
        "type": "tweet",
        "id": "2107466274724876672",
        "url": "https://x.com/MistralAI/status/2107466274724876672",
        "text": "Meet Mistral Large 4",
        "createdAt": "Mon Oct 05 13:41:29 +0000 2026",
        "lang": "en",
        "isReply": False,
        "likeCount": 12188,
        "retweetCount": 900,
        "replyCount": 300,
        "quoteCount": 50,
        "viewCount": 900000,
        "author": {"userName": "MistralAI", "id": 1234, "followers": 500000},
    }
    return {**item, **overrides}


# ── Queries ──────────────────────────────────────────────────────────────────


def test_chunks_keep_every_handle_and_fit_the_limit():
    handles = [f"account_number_{i}" for i in range(40)]
    chunks = x_sources.chunk_handles(handles, max_chars=200)
    assert [h for c in chunks for h in c] == handles
    assert len(chunks) > 1
    assert all(len(x_sources.from_clause(c)) <= 200 for c in chunks)


def test_chunks_are_balanced_not_one_full_query_and_a_tiny_one():
    # Each clause of n of these handles is 17n - 2 characters: 22 fit in 380, 23 do not.
    handles = [f"handle{i:02d}" for i in range(23)]
    sizes = [len(c) for c in x_sources.chunk_handles(handles, max_chars=380)]
    assert sizes == [12, 11]


def test_apify_query_bounds_the_window_in_unix_seconds():
    q = x_sources.apify_query(["a", "b"], START, END)
    assert q.startswith("(from:a OR from:b) -filter:retweets -filter:replies")
    assert f"since_time:{int(START.timestamp())}" in q
    assert f"until_time:{int(END.timestamp())}" in q


# ── Parsing ──────────────────────────────────────────────────────────────────


def test_parse_keeps_a_normal_post():
    posts, stats = x_sources.parse_apify_items([apify_item()], START, END)
    assert stats == {"mock": 0, "dropped": 0}
    p = posts[0]
    assert p["post_id"] == 2107466274724876672
    assert p["author_handle"] == "MistralAI" and p["author_id"] == "1234"
    assert p["created_at"] == datetime(2026, 10, 5, 13, 41, 29, tzinfo=UTC)
    assert p["likes"] == 12188 and p["author_followers"] == 500000


def test_parse_counts_mock_filler_separately():
    # The actor pads short results with mock_tweet items to reach its minimum charge.
    mock = {"type": "mock_tweet", "id": -1, "text": "From KaitoEasyAPI, a reminder"}
    posts, stats = x_sources.parse_apify_items([apify_item(), mock, mock], START, END)
    assert len(posts) == 1
    assert stats == {"mock": 2, "dropped": 0}


def test_parse_drops_replies_reposts_and_posts_outside_the_window():
    items = [
        apify_item(isReply=True),
        apify_item(retweeted_tweet={"id": "1"}),
        apify_item(createdAt="Tue Oct 06 00:00:00 +0000 2026"),  # end is exclusive
    ]
    posts, stats = x_sources.parse_apify_items(items, START, END)
    assert posts == []
    assert stats == {"mock": 0, "dropped": 3}


# ── Runner ───────────────────────────────────────────────────────────────────


def test_fetch_stops_at_the_budget(monkeypatch):
    calls = []

    def fake_fetch(handles, start, end, label, job, max_items=250, max_charge_usd=0.1, replay=True):
        calls.append(label)
        meta = {"source": "apify", "job": job, "est_cost_usd": 0.08, "max_items": max_items}
        return {"meta": meta, "items": []}, False

    monkeypatch.setattr(x_sources, "fetch_apify", fake_fetch)
    monkeypatch.setattr(x_sources, "counts_in_reach", lambda start: False)
    monkeypatch.setattr(x_store, "last_fetched_until", lambda label: None)
    monkeypatch.setattr(x_store, "record_run", lambda *a, **k: 1)
    monkeypatch.setattr(x_store, "upsert_posts", lambda *a, **k: None)

    with pytest.raises(run_x_refresh.BudgetExceeded):
        run_x_refresh.fetch(END, timedelta(hours=26), spent=[0.0], budget=0.10)
    assert len(calls) == 2  # $0.08, then $0.16, then stop before the third query


def test_a_query_fetches_from_where_its_last_fetch_ended(monkeypatch):
    now = END
    monkeypatch.setattr(x_store, "last_fetched_until", lambda label: now - timedelta(hours=3))
    assert run_x_refresh.fetch_window("ai1", now, timedelta(hours=26)) == (
        now - timedelta(hours=3),
        now,
    )


def test_a_query_never_reaches_back_further_than_the_lookback(monkeypatch):
    now = END
    monkeypatch.setattr(x_store, "last_fetched_until", lambda label: now - timedelta(days=9))
    start, _ = run_x_refresh.fetch_window("ai1", now, timedelta(hours=26))
    assert start == now - timedelta(hours=26)
    monkeypatch.setattr(x_store, "last_fetched_until", lambda label: None)
    start, _ = run_x_refresh.fetch_window("ai9", now, timedelta(hours=26))
    assert start == now - timedelta(hours=26)


def test_briefings_are_kept_when_their_posts_have_not_changed(monkeypatch):
    posts = [scored_post("1", "OpenAI", "ai", 0.9)]
    monkeypatch.setattr(x_digest, "daily_candidates", lambda section, end: (posts, []))
    monkeypatch.setattr(
        x_store, "latest_digest", lambda section, period: {"candidate_ids": [1], "edition_at": END}
    )
    written = []
    monkeypatch.setattr(x_digest, "write_daily", lambda *a, **k: written.append(a))
    run_x_refresh.write(END, weekly=False, force=False)
    assert written == []


def test_et_label_names_the_moment_in_new_york_time():
    assert x_digest.et_label(datetime(2026, 10, 6, 13, 35, tzinfo=UTC)) == (
        "Tuesday, October 6, 9:35 a.m. ET"
    )


def test_rebuild_refuses_a_remote_database(monkeypatch):
    monkeypatch.setattr(
        x_store.db_connection, "DSN", "postgresql://u:p@example.proxy.rlwy.net:5432/railway"
    )
    with pytest.raises(SystemExit, match="only runs against a local database"):
        run_x_refresh.rebuild()


# ── Attachments ──────────────────────────────────────────────────────────────


def test_parse_keeps_media_links_and_the_quoted_post():
    item = apify_item(
        text="Read this https://t.co/link https://t.co/pic",
        entities={
            "urls": [
                {
                    "url": "https://t.co/link",
                    "expanded_url": "https://mistral.ai/news",
                    "display_url": "mistral.ai/news",
                }
            ]
        },
        extendedEntities={
            "media": [
                {
                    "type": "photo",
                    "url": "https://t.co/pic",
                    "media_url_https": "https://pbs.twimg.com/media/a.jpg",
                    "expanded_url": "https://x.com/MistralAI/status/1/photo/1",
                }
            ]
        },
        quoted_tweet={
            "id": "99",
            "text": "Original",
            "url": "https://x.com/a/status/99",
            "author": {"userName": "a"},
        },
    )
    (post,), _ = x_sources.parse_apify_items([item], START, END)
    assert post["links"] == [
        {"tco": "https://t.co/link", "url": "https://mistral.ai/news", "display": "mistral.ai/news"}
    ]
    assert post["media"][0]["type"] == "photo"
    assert post["media"][0]["image"] == "https://pbs.twimg.com/media/a.jpg"
    assert post["quoted"]["author"] == "a" and post["quoted"]["text"] == "Original"
    assert post["quoted_post_id"] == 99


# ── Jev ──────────────────────────────────────────────────────────────────────


def test_post_state_expands_links_and_describes_attachments():
    post = {
        "author_handle": "MistralAI",
        "author_followers": 220146,
        "text": "Big day &amp; more https://t.co/link https://t.co/pic",
        "links": [{"tco": "https://t.co/link", "display": "mistral.ai/news"}],
        "media": [{"type": "photo", "tco": "https://t.co/pic"}, {"type": "photo", "tco": None}],
        "quoted": {"author": "a", "text": "Original post", "links": [], "media": []},
    }
    state = x_jev.post_state(post)
    assert "Author: @MistralAI (220,146 followers)" in state
    assert "Post: Big day & more mistral.ai/news" in state
    assert "t.co" not in state
    assert "Attached: 2 photo" in state
    assert "Quoting @a: Original post" in state


def test_rank_v1_uses_the_group_as_section_fit():
    scores = {"news": 0.9, "substance": 0.2, "promo": 0.1, "ai": 1.0, "macro": 0.0}
    assert x_jev.rank_score(scores, "ai", "v1") == pytest.approx(1.0 * 0.9 * 0.9)
    assert x_jev.rank_score(scores, "macro", "v1") == 0.0
    # Wire posts fit if they are about either section.
    assert x_jev.rank_score(scores, "wire", "v1") == pytest.approx(1.0 * 0.9 * 0.9)


def test_rank_v3_is_the_sections_worth_score():
    scores = {"ai_daily": 0.7, "macro_daily": 0.2}
    assert x_jev.rank_score(scores, "ai", "v3") == 0.7
    assert x_jev.rank_score(scores, "macro", "v3") == 0.2
    assert x_jev.rank_score(scores, "wire", "v3") == 0.7
    assert x_jev.rank_score(None, "ai", "v3") is None


def test_every_question_set_ranks_its_own_scores():
    # A set whose rank reads a question it doesn't ask would fail on real labels.
    for name, qs in x_jev.QSETS.items():
        scores = dict.fromkeys(qs["questions"], 0.5)
        for group in ("ai", "macro", "wire"):
            assert 0.0 <= x_jev.rank_score(scores, group, name) <= 1.0


def test_judging_is_refused_on_a_remote_database(monkeypatch):
    from fastapi.testclient import TestClient

    import api

    monkeypatch.setattr(x_store, "is_local_db", lambda: False)
    response = TestClient(api.app).post("/x/judgments", json={"post_id": "1", "verdict": "keep"})
    assert response.status_code == 403


# ── Digests ──────────────────────────────────────────────────────────────────


def scored_post(post_id, author, group, worth, text="A post long enough to count as content"):
    return {
        "post_id": post_id,
        "author_handle": author,
        "group_name": group,
        "active": True,
        "text": text,
        "links": [],
        "media": [],
        "quoted": None,
        "scores": {"ai_daily": worth, "macro_daily": worth},
    }


def test_select_ranks_caps_each_author_and_skips_empty_posts():
    posts = [
        scored_post("1", "emollick", "ai", 0.9),
        scored_post("2", "emollick", "ai", 0.8),
        scored_post("3", "emollick", "ai", 0.7),
        scored_post("4", "OpenAI", "ai", 0.6),
        {  # an image post with no text
            **scored_post("5", "MistralAI", "ai", 0.95, text="https://t.co/pic"),
            "media": [{"type": "photo", "tco": "https://t.co/pic"}],
        },
        scored_post("6", "ecb", "macro", 0.99),  # other group
        {**scored_post("7", "_akhaliq", "ai", 0.99), "active": False},  # dropped account
    ]
    picked = x_digest.select(posts, "ai", limit=10, per_author=2)
    assert [p["post_id"] for p in picked] == ["1", "2", "4"]


def test_a_quoted_post_already_in_the_list_is_referenced_not_repeated():
    original = scored_post("1", "MistralAI", "ai", 0.9, text="Meet Mistral Large 4, our new model")
    quote = {
        **scored_post("2", "natolambert", "ai", 0.8, text="A chonky week for open models"),
        "quoted": {"post_id": "1", "author": "MistralAI", "text": "Meet Mistral Large 4"},
    }
    elsewhere = {
        **scored_post("3", "simonw", "ai", 0.7, text="Look at this experiment I ran"),
        "quoted": {"post_id": "99", "author": "a", "text": "word " * 200},
    }
    block, refs = x_digest._post_blocks([original, quote, elsewhere], [], with_date=False)
    assert "(quoting [1])" in block
    assert block.count("Meet Mistral Large 4") == 1
    quoted_line = next(ln for ln in block.splitlines() if ln.startswith("(quoting @a:"))
    assert len(quoted_line) < x_digest.QUOTE_CHARS + 30
    assert refs == {1: "1", 2: "2", 3: "3"}


def test_parse_daily_reads_the_lead_and_also_lines_and_maps_citations():
    reply = """Title: Mistral ships Large 4

    Mistral released Large 4 [2][1]. It is
    open weights [2].

    Also:
    - A made-up claim [99].
    - Yields rose [3]."""
    content = x_digest.parse_daily(reply, {1: "111", 2: "222", 3: "333"})
    assert content["title"] == "Mistral ships Large 4"
    assert content["paragraphs"] == [
        "Mistral released Large 4 [[222]][[111]]. It is open weights [[222]]."
    ]
    assert content["also"] == ["A made-up claim.", "Yields rose [[333]]."]
    assert content["post_ids"] == ["222", "111", "333"]


def test_parse_daily_needs_a_title_and_a_lead():
    assert x_digest.parse_daily("Only one block", {}) is None
    assert x_digest.parse_daily("Title\n\nAlso:\n- Only also lines [1].", {1: "1"}) is None
    assert x_digest.parse_daily(None, {}) is None


def test_parse_weekly_reads_stories_as_heading_and_text():
    reply = """Open models, new rules

    Open models catch up
    Mistral and Reflection released strong open models [1][2].

    Regulation reaches the product:
    OpenAI will watermark text in the EU [3]."""
    content = x_digest.parse_weekly(reply, {1: "1", 2: "2", 3: "3"})
    assert content["title"] == "Open models, new rules"
    assert content["stories"] == [
        {
            "heading": "Open models catch up",
            "text": "Mistral and Reflection released strong open models [[1]][[2]].",
        },
        {
            "heading": "Regulation reaches the product",
            "text": "OpenAI will watermark text in the EU [[3]].",
        },
    ]
    assert x_digest.plain_text(content) == (
        "Open models catch up: Mistral and Reflection released strong open models. "
        "Regulation reaches the product: OpenAI will watermark text in the EU."
    )


def test_strip_citations_leaves_plain_prose():
    assert x_digest.strip_citations("Yields rose [[333]] [[1]].") == "Yields rose."


def test_showcase_takes_the_best_post_per_account():
    posts = [
        scored_post("1", "OpenAI", "ai", 0.9),
        scored_post("2", "OpenAI", "ai", 0.8),
        scored_post("3", "sama", "ai", 0.7),
        scored_post("4", "ecb", "macro", 0.6),
    ]
    assert x_digest.showcase(posts, {"ai": 2, "macro": 1}) == ["1", "3", "4"]


def test_the_prompt_examples_parse_as_their_own_format():
    # An example the parser rejects would teach the writer a shape that fails.
    refs = {i: str(i) for i in range(1, 20)}
    for section, cfg in x_digest.SECTIONS.items():
        daily, weekly = cfg["examples"]
        assert x_digest.parse_daily(daily, refs)["also"], section
        assert len(x_digest.parse_weekly(weekly, refs)["stories"]) == 3, section


def test_the_raw_archive_is_kept_only_with_the_local_database(monkeypatch, tmp_path):
    path = tmp_path / "apify" / "2026-10-05" / "ai1_abc.json"
    monkeypatch.setattr(
        x_sources.db_connection, "DSN", "postgresql://u:p@example.proxy.rlwy.net:5432/railway"
    )
    x_sources.write_archive(path, {"meta": {}})
    assert not path.exists()
    monkeypatch.setattr(x_sources.db_connection, "DSN", "postgresql://u:p@db:5432/stocknews")
    x_sources.write_archive(path, {"meta": {}})
    assert path.exists()
