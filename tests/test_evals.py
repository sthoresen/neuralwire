"""Evals: scoring runs from verdicts."""

import pytest

from evals import store


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
