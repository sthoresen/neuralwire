"""Eval playground: filling prompts, reading yes/no answers and scoring them against labels."""

from evals.playground import core


def test_placeholders_are_named_fields_and_escaped_braces_are_not():
    prompt = 'Article: {headline}\nReply {{"is_event": true}} or {"is_event": false}. {headline} {ticker}'
    assert core.placeholders(prompt) == ["headline", "ticker"]


def test_fill_leaves_json_examples_alone_and_unescapes_doubled_braces():
    filled = core.fill(
        '{headline} | {{"a": 1}} | {"b": 2} | {missing}', {"headline": "Nvidia buys Groq"}
    )
    assert filled == 'Nvidia buys Groq | {"a": 1} | {"b": 2} | {missing}'


def test_a_case_keeps_its_own_id_or_gets_a_stable_hash():
    assert core.case_id({"id": 7, "x": 1}) == "7"
    assert core.case_id({"x": 1, "y": 2}) == core.case_id({"y": 2, "x": 1})
    assert core.case_id({"x": 1}) != core.case_id({"x": 2})


def test_answers_from_json_fields_first_words_and_failures():
    assert core.answer('```json\n{"is_event": true, "title": "x"}\n```') == "YES"
    assert core.answer('{"title": "x", "is_event": false}') == "NO"
    assert core.answer('{"confidence": "high", "is_event": "yes"}', field="is_event") == "YES"
    assert core.answer('{"is_event": true}', field="other") == "?"
    assert core.answer("No. It is a price target change.") == "NO"
    assert core.answer("Yes") == "YES"
    assert core.answer("Maybe, it depends.") == "?"
    assert core.answer(None) == "?"


def test_score_counts_only_labelled_cases_and_unreadable_as_wrong():
    answers = {"a": "YES", "b": "NO", "c": "YES", "d": "?", "e": "YES"}
    labels = {"a": "YES", "b": "YES", "c": "NO", "d": "NO"}  # e is unlabelled
    s = core.score(answers, labels)
    assert (s["judged"], s["yes_found"], s["yes_total"], s["false_yes"], s["unreadable"]) == (
        4,
        1,
        2,
        1,
        1,
    )
    assert s["accuracy"] == 0.25
    assert core.score({}, {})["accuracy"] is None


def test_a_prompt_without_placeholders_runs_once_per_model(tmp_path, monkeypatch):
    from evals.playground import server

    monkeypatch.setattr(server, "DATASETS", tmp_path)
    server.save_dataset("demo", [{"headline": "A" * 300, "n": 1}, {"headline": "B", "n": 2}])
    plain = server.check_prompt("What's the capital of France?", "demo", {})
    filled = server.check_prompt("Summarise {headline} for {ticker}", "demo", {})
    assert plain["calls_per_model"] == 1
    assert filled["calls_per_model"] == 2
    assert filled["missing"] == ["ticker"]
    assert len(filled["sample"][0]["headline"]) == 241  # long values are clipped in the preview
