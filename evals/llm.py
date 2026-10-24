"""
LLM calls for evals: one named model per call, through OpenRouter, with the exact cost and
latency of each call and a disk cache keyed by the full request.

The cache makes reruns of an unchanged variant free. A cached answer keeps the cost and latency
of the call that produced it, so a leaderboard shows what a variant costs to run, not what this
particular run happened to spend.
"""

import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass

import openai
from dotenv import dotenv_values

from evals import EVALS_DIR, REPO_DIR

CACHE_DIR = EVALS_DIR / ".cache" / "llm"
OPENROUTER = "https://openrouter.ai/api/v1"
# Out of credits, a bad key or an unknown model: retrying can't help.
FATAL_STATUSES = {401, 402, 403, 404}

_client = None
_client_lock = threading.Lock()


@dataclass
class Completion:
    text: str | None
    model: str
    cost_usd: float
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    reasoning_tokens: int = 0
    cached: bool = False


def _get_client() -> openai.OpenAI:
    global _client
    with _client_lock:
        if _client is None:
            key = dotenv_values(REPO_DIR / "python_backend" / ".env").get("OPENROUTER_KEY")
            if not key:
                raise RuntimeError("OPENROUTER_KEY is not set in python_backend/.env")
            _client = openai.OpenAI(base_url=OPENROUTER, api_key=key, max_retries=0)
        return _client


def _cache_key(request: dict) -> str:
    return hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()[:24]


def complete(
    model: str,
    prompt: str,
    max_tokens: int = 1000,
    temperature: float = 0.0,
    reasoning: bool | str | None = False,
) -> Completion:
    """
    One chat completion. reasoning=None leaves the model's default, True/False turns it on or
    off, and an effort ("minimal", "low", "medium", "high") sets how much. Raises RuntimeError
    when the call can't succeed (no credits, bad model).
    """
    request = {
        "model": model,
        "prompt": prompt,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "reasoning": reasoning,
    }
    path = CACHE_DIR / f"{_cache_key(request)}.json"
    if path.exists():
        return Completion(**{**json.loads(path.read_text()), "cached": True})

    extra_body: dict = {"usage": {"include": True}}
    if isinstance(reasoning, str):
        extra_body["reasoning"] = {"effort": reasoning}
    elif reasoning is not None:
        extra_body["reasoning"] = {"enabled": reasoning}

    t0 = time.perf_counter()
    for attempt in range(3):
        try:
            resp = _get_client().chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=extra_body,
            )
            break
        except openai.APIStatusError as e:
            if e.status_code in FATAL_STATUSES:
                raise RuntimeError(f"{model}: {e.status_code} {str(e)[:200]}") from e
            time.sleep(2**attempt)
        except openai.APIConnectionError:
            time.sleep(2**attempt)
    else:
        raise RuntimeError(f"{model}: no response after 3 attempts")

    usage = resp.usage
    extra = (getattr(usage, "model_extra", None) or {}) if usage else {}
    text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
    details = getattr(usage, "completion_tokens_details", None) if usage else None
    result = Completion(
        text=text or None,
        model=model,
        cost_usd=float(extra.get("cost") or 0.0),
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        latency_s=round(time.perf_counter() - t0, 3),
        reasoning_tokens=(getattr(details, "reasoning_tokens", 0) or 0) if details else 0,
    )
    if result.text:  # an empty reply is worth retrying next time, not caching
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:  # the first answer stays; a concurrent run's later one doesn't replace it
            with path.open("x") as f:
                f.write(json.dumps({k: v for k, v in asdict(result).items() if k != "cached"}))
        except FileExistsError:
            pass
    return result
