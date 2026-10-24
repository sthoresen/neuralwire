"""Event pipeline: the scan with memory, month readiness, selection, verification and repeats."""

from datetime import date

import event_pipeline as ep

TODAY = date(2026, 10, 20)


def analysis(i, published, headline="Nvidia agrees to buy Groq"):
    return {
        "id": i,
        "article_id": 100 + i,
        "ticker": "NVDA",
        "headline": headline,
        "impact_headline": headline,
        "ai_summary": headline,
        "published_at": published,
    }


class FakeLLM:
    """
    Replies by step, from lists of replies (None means no model answered); verify raises like
    llms.LLMProviderManager.call_model does on failure. Records every prompt per step.
    """

    def __init__(self, scan=(), select=(), verify=(), same=()):
        self.replies = {
            "scan": list(scan),
            "select": list(select),
            "verify": list(verify),
            "same": list(same),
        }
        self.prompts = {k: [] for k in self.replies}

    def _next(self, step, prompt):
        self.prompts[step].append(prompt)
        return self.replies[step].pop(0) if self.replies[step] else None

    def call(self, prompt, tier="standard", **kwargs):
        if "first filter" in prompt:
            step = "scan"
        elif "Do these two entries" in prompt:
            step = "same"
        else:
            step = "select"
        reply = self._next(step, prompt)
        return (reply, "fake") if reply is not None else (None, None)

    def call_model(self, model_id, prompt, **kwargs):
        reply = self._next("verify", prompt)
        if reply is None:
            raise RuntimeError("no reply")
        return reply, model_id


EVENT = (
    '{"is_event": true, "title": "Nvidia buys Groq", "event_date": "2026-03-20", '
    '"event_date_label": "March 20, 2026", "description": "Nvidia agreed to buy Groq."}'
)
NO_EVENT = '```json\n{"is_event": false}\n```'


def verified(day, title="Nvidia licenses Groq technology", url="u1", precision="day"):
    return (
        f'{{"status": "happened", "event_date": "{day}", "precision": "{precision}", '
        f'"title": "{title}", "description": "One sentence.", "evidence_url": "{url}"}}'
    )


# ── Scan ─────────────────────────────────────────────────────────────────────


def test_an_event_reply_becomes_a_candidate_for_its_article_month():
    candidate, answered = ep.scan_one(
        analysis(1, "2026-03-20"), "(none yet)", "(none)", FakeLLM([EVENT])
    )
    assert answered
    assert candidate["title"] == "Nvidia buys Groq"
    assert candidate["month"] == "2026-03"
    assert (candidate["analysis_run_id"], candidate["article_id"]) == (1, 101)


def test_a_fenced_no_event_reply_is_answered_without_a_candidate():
    assert ep.scan_one(analysis(1, "2026-03-20"), "", "(none)", FakeLLM([NO_EVENT])) == (None, True)


def test_no_reply_is_not_answered():
    assert ep.scan_one(analysis(1, "2026-03-20"), "", "(none)", FakeLLM([None])) == (None, False)


def test_analyses_without_a_reply_stay_unchecked():
    store = ep.MemoryStore("NVDA", [analysis(1, "2026-03-01"), analysis(2, "2026-03-02")], [])
    stats = ep.run_scan(store, FakeLLM([None, NO_EVENT]))
    assert (stats["scanned"], stats["no_reply"]) == (1, 1)
    assert [a["id"] for a in store.unchecked(10)] == [1]


def test_later_scans_see_what_was_flagged_within_the_memory_window():
    analyses = [analysis(1, "2026-03-01"), analysis(2, "2026-03-10"), analysis(3, "2026-05-01")]
    llm = FakeLLM([EVENT, NO_EVENT, NO_EVENT])
    ep.run_scan(ep.MemoryStore("NVDA", analyses, []), llm)
    first, second, third = llm.prompts["scan"]
    assert "2026-03-01 | Nvidia buys Groq" not in first
    assert "2026-03-01 | Nvidia buys Groq" in second
    assert "Nvidia buys Groq" not in third  # two months later: outside the 30 days


# ── Month readiness ──────────────────────────────────────────────────────────


def _flagged_store(published, scan=None):
    """A store with an analysis per date; the first `scan` of them scanned and flagged."""
    store = ep.MemoryStore("NVDA", [analysis(i, p) for i, p in enumerate(published, start=1)], [])
    n = len(published) if scan is None else scan
    ep.run_scan(store, FakeLLM([EVENT] * n), batch_size=n)
    return store


def test_a_month_is_ready_when_it_is_over_and_fully_scanned():
    store = _flagged_store(["2026-03-05", "2026-03-25", "2026-10-02"], scan=1)
    assert store.months_ready(TODAY) == []  # March still has an unchecked analysis
    ep.run_scan(store, FakeLLM([NO_EVENT, EVENT]))
    assert store.months_ready(TODAY) == ["2026-03"]  # October has a candidate but isn't over


# ── Selection ────────────────────────────────────────────────────────────────


def _candidates(n):
    return [
        {
            "id": i,
            "article_id": 100 + i,
            "title": f"Event {i}",
            "event_date": f"2026-04-0{i}",
            "headline": "h",
            "published_at": f"2026-04-0{i}",
        }
        for i in range(1, n + 1)
    ]


def test_selection_keeps_at_most_cap_known_distinct_candidates():
    reply = (
        '[{"id": "c2", "title": "Two"}, {"id": "c9"}, {"id": "[c1]"}, {"id": "c2"}, '
        '{"id": "c3", "title": "Three"}]'
    )
    picks = ep.select_month("NVDA", "2026-04", _candidates(4), [], 2, FakeLLM(select=[reply]))
    assert [(c["id"], p.get("title")) for c, p in picks] == [(2, "Two"), (1, None)]


def test_selection_without_a_reply_is_none():
    assert ep.select_month("NVDA", "2026-04", _candidates(2), [], 2, FakeLLM()) is None


def test_a_single_object_reply_still_selects():
    picks = ep.select_month(
        "NVDA", "2026-04", _candidates(1), [], 3, FakeLLM(select=['{"id": "c1"}'])
    )
    assert len(picks) == 1


# ── Verification ─────────────────────────────────────────────────────────────

CANDIDATE = {
    "id": 7,
    "article_id": 799,
    "title": "Nvidia acquires Groq",
    "event_date": "2026-03-20",
    "headline": "Chip sector pressure",
    "published_at": "2026-03-20 10:00:00",
}


def test_verification_redates_an_event_and_corrects_its_wording():
    llm = FakeLLM(verify=[verified("2026-03-02")])
    event, why = ep.verify("NVDA", CANDIDATE, {"why": "big"}, llm)
    assert why == "ok"
    assert (event["event_date"], event["event_date_label"]) == ("2026-03-02", "March 2, 2026")
    assert event["title"] == "Nvidia licenses Groq technology"
    assert (event["candidate_id"], event["scan_date"]) == (7, "2026-03-20")


def test_a_month_precision_date_gets_a_month_label():
    llm = FakeLLM(verify=[verified("2026-03-01", precision="month")])
    event, _ = ep.verify("NVDA", CANDIDATE, {}, llm)
    assert event["event_date_label"] == "March 2026"


def test_verification_reasons():
    assert (
        ep.verify("NVDA", CANDIDATE, {}, FakeLLM(verify=['{"status": "unconfirmed"}']))[1]
        == "unconfirmed"
    )
    assert (
        ep.verify("NVDA", CANDIDATE, {}, FakeLLM(verify=["I could not search."]))[1] == "unreadable"
    )
    assert ep.verify("NVDA", CANDIDATE, {}, FakeLLM())[1] == "no_reply"


# ── Repeats ──────────────────────────────────────────────────────────────────


def _event(title, day, url=None):
    return {"title": title, "event_date": day, "event_date_label": day, "evidence_url": url}


def test_only_events_close_in_date_are_compared():
    llm = FakeLLM(same=["same", "same"])
    events = [_event("Rubin unveiled", "2026-03-16"), _event("Rubin in production", "2026-06-01")]
    kept, repeats = ep.drop_repeats("NVDA", events, [], llm)
    assert (len(kept), repeats) == (2, [])
    assert llm.prompts["same"] == []  # eleven weeks apart: never asked


def test_a_repeat_of_the_timeline_or_a_shared_evidence_page_is_dropped():
    timeline = [{"title": "Q4 results", "description": "", "event_date": "2026-02-25"}]
    events = [
        _event("Q4 results again", "2026-02-26"),
        _event("Intel stake", "2025-09-18", url="u"),
        _event("Intel stake, 10%", "2025-09-30", url="u"),
    ]
    kept, repeats = ep.drop_repeats("NVDA", events, timeline, FakeLLM(same=["Same.", "same"]))
    assert [e["title"] for e in kept] == ["Intel stake"]
    assert [(e["title"], of) for e, of in repeats] == [
        ("Intel stake, 10%", "Intel stake"),
        ("Q4 results again", "Q4 results"),
    ]


def test_a_repeat_check_without_a_reply_is_none():
    timeline = [{"title": "Q4 results", "description": "", "event_date": "2026-02-25"}]
    assert ep.drop_repeats("NVDA", [_event("Q4", "2026-02-25")], timeline, FakeLLM()) is None


# ── A whole month ────────────────────────────────────────────────────────────


def _ready_store(n):
    store = _flagged_store([f"2026-04-0{i}" for i in range(1, n + 1)])
    assert store.months_ready(TODAY) == ["2026-04"]
    return store


def test_a_month_adds_verified_picks_and_decides_every_candidate():
    store = _ready_store(3)
    llm = FakeLLM(
        select=['[{"id": "c1"}, {"id": "c2"}]'],
        verify=[verified("2026-04-01", url="a"), '{"status": "unconfirmed"}'],
    )
    stats = ep.run_select(store, llm, TODAY)
    assert (stats["events_added"], stats["unconfirmed"]) == (1, 1)
    assert [c["status"] for c in store.candidates] == ["selected", "unconfirmed", "not_selected"]
    assert store.events[0]["evidence_url"] == "a"
    assert store.months_ready(TODAY) == []


def test_late_arrivals_only_fill_what_is_left_of_the_cap():
    store = _ready_store(1)
    ep.run_select(
        store, FakeLLM(select=['[{"id": "c1"}]'], verify=[verified("2026-04-01")]), TODAY, cap=2
    )
    for i in (8, 9):  # two April articles, analysed late
        store.analyses.append({**analysis(i, f"2026-04-1{i - 3}"), "checked": False})
    ep.run_scan(store, FakeLLM([EVENT, EVENT]))
    llm = FakeLLM(
        select=['[{"id": "c1"}, {"id": "c2"}]'],
        verify=[verified("2026-04-15", url="b"), verified("2026-04-16", url="c")],
    )
    ep.run_select(store, llm, TODAY, cap=2)
    assert len(llm.prompts["verify"]) == 1  # one slot was left, and the first pick filled it
    assert [c["status"] for c in store.candidates] == ["selected", "selected", "not_selected"]


def test_a_failed_pick_leaves_its_slot_to_the_next_in_line():
    store = _ready_store(3)
    llm = FakeLLM(
        select=['[{"id": "c1"}, {"id": "c2"}, {"id": "c3"}]'],
        verify=[verified("2025-09-18"), verified("2026-04-02", url="b")],  # c1 is an old event
    )
    stats = ep.run_select(store, llm, TODAY, cap=1)
    assert "at most 4 events" in llm.prompts["select"][0]  # the room and 3 spares
    assert (stats["events_added"], stats["too_old"]) == (1, 1)
    assert [c["status"] for c in store.candidates] == ["too_old", "selected", "not_selected"]


def test_a_repeat_also_leaves_its_slot_to_the_next_in_line():
    store = _ready_store(2)
    store.events.append({"title": "Q4 results", "description": "", "event_date": "2026-04-01"})
    llm = FakeLLM(
        select=['[{"id": "c1"}, {"id": "c2"}]'],
        verify=[verified("2026-04-01", url="a"), verified("2026-04-20", url="b")],
        same=["same"],
    )
    ep.run_select(store, llm, TODAY, cap=1)
    assert [c["status"] for c in store.candidates] == ["repeat", "selected"]


def test_a_full_month_rejects_late_arrivals_without_asking():
    store = _ready_store(1)
    ep.run_select(
        store, FakeLLM(select=['[{"id": "c1"}]'], verify=[verified("2026-04-01")]), TODAY, cap=1
    )
    store.analyses.append({**analysis(9, "2026-04-15"), "checked": False})
    ep.run_scan(store, FakeLLM([EVENT]))
    llm = FakeLLM()
    ep.run_select(store, llm, TODAY, cap=1)
    assert llm.prompts["select"] == []
    assert store.candidates[-1]["status"] == "not_selected"


def test_a_month_with_a_failed_call_stays_pending_for_the_next_run():
    store = _ready_store(2)
    stats = ep.run_select(store, FakeLLM(select=['[{"id": "c1"}]']), TODAY)  # verify gets no reply
    assert stats["months_retried"] == 1
    assert store.events == []
    assert {c["status"] for c in store.candidates} == {"pending"}
    assert store.months_ready(TODAY) == ["2026-04"]


# ── Only recent events ───────────────────────────────────────────────────────


def test_a_month_too_far_back_is_not_selected():
    store = _flagged_store(["2024-08-06"])  # an old article, analysed now
    llm = FakeLLM()
    stats = ep.run_select(store, llm, TODAY)
    assert stats["months_too_old"] == 1
    assert llm.prompts["select"] == []
    assert store.candidates[0]["status"] == "too_old"


def test_an_old_event_mentioned_in_a_new_article_is_dropped():
    assert (
        ep.verify("NVDA", CANDIDATE, {}, FakeLLM(verify=[verified("2025-09-18")]))[1] == "too_old"
    )
    late_by_days = ep.verify("NVDA", CANDIDATE, {}, FakeLLM(verify=[verified("2026-02-27")]))
    assert late_by_days[1] == "ok"  # 21 days before the article: still recent


def test_a_dropped_old_event_is_marked_too_old():
    store = _ready_store(2)
    llm = FakeLLM(select=['[{"id": "c1"}]'], verify=[verified("2025-09-18")])
    stats = ep.run_select(store, llm, TODAY)
    assert (stats["too_old"], stats["events_added"]) == (1, 0)
    assert [c["status"] for c in store.candidates] == ["too_old", "not_selected"]
