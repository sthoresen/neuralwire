"""Evals: scoring runs from verdicts, and the monthly selection's cap."""

import pytest

from evals import store
from evals.problems import events


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)


def _run(variant, keys):
    r = store.Run("events", variant, "ds", {"m": "x"}, "p1", {})
    for k in keys:
        r.add_output(k, title=k)
    r.save()
    return r


def test_the_latest_verdict_wins_and_clearing_removes_it():
    store.add_label("events", "a", "you", "keep")
    store.add_label("events", "a", "you", "skip", "dup")
    store.add_label("events", "b", "you", "keep")
    store.add_label("events", "b", "you", None)
    labels = store.load_labels("events")
    assert labels[("a", "you")]["verdict"] == "skip"
    assert labels[("a", "you")]["note"] == "dup"
    assert ("b", "you") not in labels


def test_precision_per_judge_and_pooled_recall():
    _run("one", ["a", "b", "c"])
    _run("two", ["c", "d"])
    store.add_label("events", "a", "you", "keep")
    store.add_label("events", "b", "you", "skip")
    store.add_label("events", "d", "claude", "keep")
    store.add_label("events", "c", "claude", "keep")
    store.add_label("events", "c", "you", "skip")  # you override Claude: c leaves the pool

    rows = {r["variant"]: r for r in store.leaderboard("events")}
    assert rows["one"]["judged_you"] == 3
    assert rows["one"]["precision_you"] == pytest.approx(1 / 3)
    assert rows["two"]["precision_claude"] == 1.0
    # the pool is {a, d}: each run found one of the two
    assert rows["one"]["recall_pooled"] == 0.5
    assert rows["two"]["recall_pooled"] == 0.5


def test_unjudged_runs_rank_below_judged_ones_even_at_zero_precision():
    _run("judged", ["a"])
    _run("unjudged", ["z"])
    store.add_label("events", "a", "you", "skip")
    assert [r["variant"] for r in store.leaderboard("events")] == ["judged", "unjudged"]


def test_agreement_counts_keys_both_judges_saw():
    store.add_label("events", "a", "you", "keep")
    store.add_label("events", "a", "claude", "keep")
    store.add_label("events", "b", "you", "keep")
    store.add_label("events", "b", "claude", "skip")
    store.add_label("events", "c", "claude", "skip")
    assert store.agreement(store.load_labels("events")) == (1, 2)


class FakeModel:
    def __init__(self, reply):
        self.reply = reply

    def call(self, prompt, **kwargs):
        return self.reply, "fake"


def test_monthly_selection_keeps_at_most_cap_known_candidates():
    candidates = [
        {"title": f"Event {i}", "event_date": f"2026-04-0{i}", "source_article_ids": [i]}
        for i in range(1, 5)
    ]
    reply = (
        '[{"id": "c2", "title": "Two"}, {"id": "c9", "title": "Made up"}, '
        '{"id": "[c1]", "title": "One"}, {"id": "c3", "title": "Three"}]'
    )
    chosen = events._select_month("NVDA", "2026-04", candidates, [], FakeModel(reply), cap=2)
    assert [c["title"] for c in chosen] == ["Two", "One"]
    assert [c["source_article_ids"] for c in chosen] == [[2], [1]]
    assert (
        chosen[0]["event_date"] == "2026-04-02"
    )  # taken from the candidate when the reply has none


def test_a_single_object_reply_still_selects():
    candidates = [{"title": "Only", "event_date": "2026-04-01", "source_article_ids": ["7"]}]
    chosen = events._select_month(
        "NVDA", "2026-04", candidates, [], FakeModel('{"id": "c1"}'), cap=3
    )
    assert len(chosen) == 1
    assert events._key("NVDA", chosen[0]) == "NVDA:7"


EVENT = {
    "title": "Nvidia acquires Groq",
    "description": "Nvidia acquired Groq.",
    "event_date": "2026-03-20",
    "source_article_ids": [799],
}
ARTICLES = {799: {"headline": "Chip sector pressure", "published_at": "2026-03-20 10:00:00"}}


def test_verification_redates_an_old_event_and_gives_it_its_own_key():
    reply = '{"status": "happened", "event_date": "2025-12-24", "precision": "day", "evidence_url": "u"}'
    event, why = events._verify_one("NVDA", EVENT, ARTICLES, FakeModel(reply))
    assert why == "ok"
    assert event["event_date"] == "2025-12-24"
    assert event["event_date_label"] == "December 24, 2025"
    assert event["scan_date"] == "2026-03-20"
    assert events._key("NVDA", event) == "NVDA:799@2025-12-24"


def test_verification_keeps_the_key_when_the_date_barely_moves():
    reply = '{"status": "happened", "event_date": "2026-03-18", "precision": "day"}'
    event, _ = events._verify_one("NVDA", EVENT, ARTICLES, FakeModel(reply))
    assert events._key("NVDA", event) == "NVDA:799"


def test_verification_drops_unconfirmed_and_unreadable_events():
    assert events._verify_one("NVDA", EVENT, ARTICLES, FakeModel('{"status": "unconfirmed"}')) == (
        None,
        "unconfirmed",
    )
    assert (
        events._verify_one("NVDA", EVENT, ARTICLES, FakeModel("I could not search."))[1]
        == "unreadable"
    )


class SameIf:
    """Answers "same" when both entries contain one of the words, and records each prompt."""

    def __init__(self, *words):
        self.words, self.prompts = words, []

    def call(self, prompt, **kwargs):
        self.prompts.append(prompt)
        a, b = prompt.split("A: ")[1].split("B: ")
        same = any(w in a and w in b for w in self.words)
        return ("Same." if same else "different"), "fake"


def _confirmed(title, day, url=None):
    return {"title": title, "event_date": day, "event_date_label": day, "evidence_url": url}


def test_only_events_close_in_date_are_compared():
    model = SameIf("Rubin")
    new = [
        _confirmed("Rubin unveiled", "2026-03-16"),
        _confirmed("Rubin in production", "2026-06-01"),
    ]
    kept, dropped = events._drop_repeats("NVDA", new, [], model)
    assert dropped == 0 and len(kept) == 2
    assert model.prompts == []  # eleven weeks apart: never asked


def test_a_repeat_of_the_timeline_or_a_shared_evidence_page_is_dropped():
    existing = [{"title": "Q4 results", "description": "", "event_date": "2026-02-25"}]
    new = [
        _confirmed("Q4 results again", "2026-02-26"),
        _confirmed("Intel stake", "2025-09-18", url="u"),
        _confirmed("Intel stake, 10%", "2025-09-30", url="u"),
        _confirmed("Bond sale", "2026-02-24"),
    ]
    kept, dropped = events._drop_repeats("NVDA", new, existing, SameIf("Q4", "Intel"))
    assert [e["title"] for e in kept] == ["Intel stake", "Bond sale"]
    assert dropped == 2
