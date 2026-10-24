"""
Jev (typesafe/jev-1.13) for evals: OpenRouter's System One endpoint, which answers questions
about a text with a probability each instead of writing text. Cached like evals.llm, and each
call is returned as a Completion so runs count its cost and latency the same way.
"""

import hashlib
import json
import time

import requests
from dotenv import dotenv_values

from evals import EVALS_DIR, REPO_DIR
from evals.llm import Completion

URL = "https://openrouter.ai/api/v1/systemone"
MODEL = "typesafe/jev-1.13"
PRICE_PER_M = 0.042  # input tokens; output is free. Used when usage carries no cost.
CACHE_DIR = EVALS_DIR / ".cache" / "jev"

_session = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        key = dotenv_values(REPO_DIR / "python_backend" / ".env").get("OPENROUTER_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_KEY is not set in python_backend/.env")
        _session = requests.Session()
        _session.headers["Authorization"] = f"Bearer {key}"
    return _session


def ask(state: str, questions: dict) -> tuple[dict[str, float] | None, Completion]:
    """({question: probability}, the call) or (None, the call) if Jev gave no answer."""
    request = {"model": MODEL, "state": state, "questions": questions}
    digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()[:24]
    path = CACHE_DIR / f"{digest}.json"
    if path.exists():
        row = json.loads(path.read_text())
        return row["scores"], Completion(**{**row["call"], "cached": True})

    t0 = time.perf_counter()
    for attempt in range(3):
        try:
            r = _get_session().post(URL, json=request, timeout=60)
        except requests.RequestException:
            time.sleep(2**attempt)
            continue
        if r.ok:
            break
        if r.status_code in (400, 401, 402, 403, 404):
            raise RuntimeError(f"jev: {r.status_code} {r.text[:300]}")
        time.sleep(2**attempt)
    else:
        return None, Completion(None, MODEL, 0.0, 0, 0, round(time.perf_counter() - t0, 3))

    out = r.json()
    usage = out.get("usage") or {}
    tokens = usage.get("input_tokens") or 0
    cost = usage.get("cost") if usage.get("cost") is not None else tokens * PRICE_PER_M / 1e6
    scores = {k: float(v["noul"]) for k, v in out["answers"].items()}
    call = Completion(
        json.dumps(scores), MODEL, float(cost), tokens, 0, round(time.perf_counter() - t0, 3)
    )
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x") as f:
            f.write(
                json.dumps(
                    {
                        "scores": scores,
                        "call": {k: v for k, v in call.__dict__.items() if k != "cached"},
                    }
                )
            )
    except FileExistsError:
        pass
    return scores, call
