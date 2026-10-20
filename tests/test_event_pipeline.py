"""Event scan: reading the cheap model's reply, and which analyses count as checked."""

import event_pipeline

ANALYSIS = {
    "id": 1,
    "article_id": 10,
    "ticker": "NVDA",
    "headline": "Nvidia agrees to buy Groq",
    "impact_headline": "Acquisition",
    "ai_summary": "Nvidia announced it will acquire Groq.",
    "published_at": "2026-10-13",
}


class FakeLLM:
    """Replies with the given texts in order; None means no provider answered."""

    def __init__(self, *replies):
        self.replies = list(replies)

    def call(self, prompt, **kwargs):
        reply = self.replies.pop(0)
        return (reply, "Fake model") if reply is not None else (None, None)


def test_an_event_reply_becomes_a_candidate():
    reply = (
        '{"is_event": true, "title": "Nvidia buys Groq", "event_date": "2026-10-13", '
        '"event_date_label": "October 13, 2026", "description": "Nvidia agreed to buy Groq."}'
    )
    candidate, answered = event_pipeline._scan_one(ANALYSIS, "(none yet)", FakeLLM(reply))
    assert answered
    assert candidate["title"] == "Nvidia buys Groq"
    assert candidate["source_article_ids"] == [10]
    assert candidate["_model"] == "Fake model"


def test_a_fenced_no_event_reply_is_answered_without_a_candidate():
    reply = '```json\n{"is_event": false}\n```'
    assert event_pipeline._scan_one(ANALYSIS, "(none yet)", FakeLLM(reply)) == (None, True)


def test_no_reply_is_not_answered():
    assert event_pipeline._scan_one(ANALYSIS, "(none yet)", FakeLLM(None)) == (None, False)


def test_analyses_without_a_reply_stay_unchecked():
    second = {**ANALYSIS, "id": 2, "article_id": 20}
    llm = FakeLLM(None, '{"is_event": false}')
    candidates, checked = event_pipeline.run_scan_pass("NVDA", [ANALYSIS, second], [], llm)
    assert candidates == []
    assert checked == [2]
