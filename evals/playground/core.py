"""
The playground's logic, kept apart from the server so it can be tested: filling a prompt with a
case's values, reading a yes/no answer out of a reply, identifying cases, and scoring a model's
answers against the labels.
"""

import hashlib
import json
import re

PLACEHOLDER = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})")
YES, NO = "YES", "NO"
_WORDS = {"yes": YES, "y": YES, "true": YES, "no": NO, "n": NO, "false": NO}


def placeholders(prompt: str) -> list[str]:
    """The {names} a prompt expects, in order of first use. {{escaped}} braces don't count."""
    return list(dict.fromkeys(PLACEHOLDER.findall(prompt)))


def fill(prompt: str, values: dict) -> str:
    """
    The prompt with each {name} replaced by the case's value. Other braces are left alone, so a
    JSON example in the prompt needs no escaping; {{ and }} (the production prompts' escaping
    for str.format) become single braces.
    """
    filled = PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), m.group(0))), prompt)
    return filled.replace("{{", "{").replace("}}", "}")


def case_id(row: dict) -> str:
    """A row's own "id" if it has one, else a hash of its content, so labels stick to the case."""
    if row.get("id") not in (None, ""):
        return str(row["id"])
    return hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _word(value) -> str | None:
    if isinstance(value, bool):
        return YES if value else NO
    if isinstance(value, str):
        return _WORDS.get(value.strip().strip(".!\"'").lower())
    return None


def _json_object(text: str):
    """The first JSON object in a reply, fenced or not, or None."""
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    break
        start = text.find("{", start + 1)
    return None


def answer(reply: str | None, field: str = "") -> str:
    """
    YES, NO or "?" for a reply. With a field name, that JSON field decides. Without one, the
    first yes/no-like value in a JSON object does, or else the reply's first word.
    """
    if not reply:
        return "?"
    data = _json_object(reply)
    if isinstance(data, dict):
        if field:
            return _word(data.get(field)) or "?"
        for value in data.values():
            if _word(value):
                return _word(value)
    elif field:
        return "?"
    first = re.match(r"\W*(\w+)", reply)
    return (first and _word(first.group(1))) or "?"


def score(answers: dict[str, str], labels: dict[str, str]) -> dict:
    """
    One model's answers ({case: YES/NO/?}) against the labels ({case: YES/NO}), over the cases
    that have both. "?" counts as wrong.
    """
    judged = [c for c in answers if labels.get(c) in (YES, NO)]
    yes = [c for c in judged if labels[c] == YES]
    found = sum(answers[c] == YES for c in yes)
    false_yes = sum(answers[c] == YES for c in judged if labels[c] == NO)
    correct = sum(answers[c] == labels[c] for c in judged)
    return {
        "judged": len(judged),
        "yes_found": found,
        "yes_total": len(yes),
        "false_yes": false_yes,
        "unreadable": sum(answers[c] == "?" for c in judged),
        "accuracy": correct / len(judged) if judged else None,
    }
