"""
LLM router tests. No network: llms.OpenAI is replaced by a fake client that
plays back scripted outcomes per model, and time.sleep records the backoff
delays instead of waiting.

Failures are real openai SDK exceptions, carrying messages copied from
production logs, so the router is tested against what OpenRouter actually sends.
"""

from types import SimpleNamespace

import httpx
import openai
import pytest

import llms

# ── Real errors ──────────────────────────────────────────────────────────────

_REQUEST = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
_SDK = openai.OpenAI(api_key="test-key", base_url="https://openrouter.ai/api/v1")


def api_error(status: int, message: str) -> openai.APIStatusError:
    """
    The exception the OpenAI SDK raises for an HTTP error response: same subclass
    (RateLimitError, NotFoundError, ...) and same "Error code: 402 - {...}" text.
    Uses the SDK's own factory, so the openai version is pinned in requirements.txt.
    """
    body = {"error": {"message": message, "code": status}}
    response = httpx.Response(status, request=_REQUEST)
    return _SDK._make_status_error(f"Error code: {status} - {body}", body=body, response=response)


# Observed in Railway logs (OpenRouter, May 2026).
NO_CREDITS = (402, "Insufficient credits. Add more using https://openrouter.ai/settings/credits")
LOW_CREDITS = (402, "This request requires more credits, or fewer max_tokens.")
DEPRECATED_MODEL = (404, "Grok 4.1 Fast is deprecated. xAI recommends switching to Grok 4.3")
REASONING_MANDATORY = (400, "Reasoning is mandatory for this endpoint and cannot be disabled.")
# Not observed in our logs; wording per OpenRouter's free-tier limit.
FREE_DAILY_LIMIT = (429, "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock more.")
RATE_LIMITED = (429, "Rate limit exceeded")
UNAVAILABLE = (503, "Service Unavailable")

# ── Fakes ────────────────────────────────────────────────────────────────────


def _completion(content):
    """Shape of an OpenAI chat completion: .choices[0].message.content."""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class FakeBackend:
    """
    Stands in for the OpenAI API. `script` maps model -> list of outcomes,
    consumed in order. An outcome is a str (reply content), an Exception
    (raised), or EMPTY_CHOICES. Unscripted calls fail with a real 503.
    """

    EMPTY_CHOICES = object()

    def __init__(self):
        self.script: dict[str, list] = {}
        self.calls: list[dict] = []
        self.clients: list[dict] = []  # kwargs each OpenAI(...) client was created with

    def client(self, **client_kwargs):
        self.clients.append(client_kwargs)
        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=self._create))
        )

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        outcomes = self.script.get(kwargs["model"], [])
        outcome = outcomes.pop(0) if outcomes else api_error(*UNAVAILABLE)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is self.EMPTY_CHOICES:
            return SimpleNamespace(choices=[])
        return _completion(outcome)

    def models_called(self) -> list[str]:
        return [c["model"] for c in self.calls]


def _provider(model, tier, supports_reasoning=True):
    return {
        "name": model,
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "test-key",
        "model": model,
        "tier": tier,
        "supports_reasoning": supports_reasoning,
        "is_active": True,
        "retries": 3,
    }


@pytest.fixture
def backend(monkeypatch):
    fake = FakeBackend()
    monkeypatch.setattr(llms, "OpenAI", fake.client)
    return fake


@pytest.fixture
def sleeps(monkeypatch):
    recorded: list[float] = []
    monkeypatch.setattr(llms.time, "sleep", recorded.append)
    return recorded


@pytest.fixture
def manager(monkeypatch, backend, sleeps):
    monkeypatch.setenv("OPENROUTER_KEY", "test-key")
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    m = llms.LLMProviderManager()
    # A fixed provider list, so tests don't depend on the real model config.
    m.providers = [
        _provider("econ-a", "economy"),
        _provider("econ-b", "economy", supports_reasoning=False),
        _provider("std-a", "standard"),
        _provider("prem-a", "premium"),
    ]
    return m


# ── Construction ─────────────────────────────────────────────────────────────


def test_importing_llms_needs_no_keys():
    # The module is already imported at the top of this file without keys set;
    # this documents that the manager, not the import, reads them.
    assert hasattr(llms, "LLMProviderManager")


@pytest.mark.parametrize("missing", ["OPENROUTER_KEY", "XAI_API_KEY"])
def test_manager_requires_both_keys(monkeypatch, missing):
    monkeypatch.setenv("OPENROUTER_KEY", "test-key")
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    monkeypatch.delenv(missing)
    with pytest.raises(OSError, match=missing):
        llms.LLMProviderManager()


# ── Routing within a tier ────────────────────────────────────────────────────


def test_first_provider_answers(manager, backend):
    backend.script["econ-a"] = ["  hello  "]

    assert manager.call("hi", tier="economy") == ("hello", "econ-a")
    assert backend.models_called() == ["econ-a"]


def test_falls_back_to_next_provider_in_same_tier(manager, backend):
    backend.script["econ-b"] = ["from b"]  # econ-a is unscripted, so it always fails

    assert manager.call("hi", tier="economy") == ("from b", "econ-b")
    assert backend.models_called() == ["econ-a"] * 3 + ["econ-b"]


def test_recovers_on_retry_without_switching_provider(manager, backend):
    backend.script["econ-a"] = [api_error(*RATE_LIMITED), "second try"]

    assert manager.call("hi", tier="economy") == ("second try", "econ-a")
    assert backend.models_called() == ["econ-a", "econ-a"]


# ── Escalation across tiers ──────────────────────────────────────────────────


def test_escalates_to_next_tier_when_allowed(manager, backend):
    backend.script["std-a"] = ["from standard"]

    assert manager.call("hi", tier="economy", max_tier="standard") == ("from standard", "std-a")


def test_does_not_escalate_without_max_tier(manager, backend):
    backend.script["std-a"] = ["from standard"]

    assert manager.call("hi", tier="economy") == (None, None)
    assert "std-a" not in backend.models_called()


def test_never_goes_above_max_tier(manager, backend):
    backend.script["prem-a"] = ["from premium"]

    assert manager.call("hi", tier="economy", max_tier="standard") == (None, None)
    assert "prem-a" not in backend.models_called()


def test_starts_at_requested_tier(manager, backend):
    backend.script["econ-a"] = ["cheap"]
    backend.script["std-a"] = ["standard"]

    assert manager.call("hi", tier="standard") == ("standard", "std-a")
    assert "econ-a" not in backend.models_called()


# ── Retries and backoff ──────────────────────────────────────────────────────


def test_backoff_doubles_between_retries(manager, backend, sleeps):
    manager.providers = [_provider("econ-a", "economy")]

    assert manager.call("hi", tier="economy") == (None, None)
    assert sleeps == [2, 4, 8]


def test_backoff_resets_for_each_provider(manager, sleeps):
    assert manager.call("hi", tier="economy") == (None, None)
    assert sleeps == [2, 4, 8, 2, 4, 8]


@pytest.mark.parametrize("bad_reply", ["", None, FakeBackend.EMPTY_CHOICES])
def test_empty_reply_is_retried(manager, backend, bad_reply):
    backend.script["econ-a"] = [bad_reply, "real answer"]

    assert manager.call("hi", tier="economy") == ("real answer", "econ-a")


def test_whitespace_only_reply_is_retried_not_returned(manager, backend):
    # A reply of just "\n" must count as empty. Otherwise it is stripped to ""
    # and returned as a success, and the router never tries another provider.
    backend.script["econ-a"] = ["\n  \n", "real answer"]

    assert manager.call("hi", tier="economy") == ("real answer", "econ-a")


# ── Error classification ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "error, expected",
    [
        (api_error(*NO_CREDITS), "disable"),
        (api_error(*LOW_CREDITS), "disable"),
        (api_error(*DEPRECATED_MODEL), "disable"),
        (api_error(401, "No auth credentials found"), "disable"),
        (api_error(*FREE_DAILY_LIMIT), "disable"),
        (api_error(*REASONING_MANDATORY), "skip"),
        (api_error(422, "Unprocessable"), "skip"),
        (api_error(*RATE_LIMITED), "retry"),
        (api_error(*UNAVAILABLE), "retry"),
        (openai.APITimeoutError(request=_REQUEST), "retry"),
        (openai.APIConnectionError(request=_REQUEST), "retry"),
        (ValueError("unexpected"), "retry"),
    ],
)
def test_classify_error(error, expected):
    assert llms.classify_error(error) == expected


@pytest.mark.parametrize("error", [NO_CREDITS, DEPRECATED_MODEL, FREE_DAILY_LIMIT])
def test_dead_provider_is_disabled_without_retrying(manager, backend, sleeps, error):
    backend.script["econ-a"] = [api_error(*error)]
    backend.script["econ-b"] = ["from b"]

    assert manager.call("hi", tier="economy") == ("from b", "econ-b")
    assert backend.models_called() == ["econ-a", "econ-b"]  # one attempt, no retries
    assert sleeps == []
    assert manager.providers[0]["is_active"] is False


def test_rejected_request_moves_on_but_keeps_provider(manager, backend, sleeps):
    # "Reasoning is mandatory" is about this request (reasoning=False), not the
    # provider: skip to the next one, but keep it for requests that allow reasoning.
    backend.script["econ-a"] = [api_error(*REASONING_MANDATORY)]
    backend.script["econ-b"] = ["from b"]

    assert manager.call("hi", tier="economy", reasoning=False) == ("from b", "econ-b")
    assert backend.models_called() == ["econ-a", "econ-b"]
    assert sleeps == []
    assert manager.providers[0]["is_active"] is True


@pytest.mark.parametrize(
    "error",
    [
        api_error(*RATE_LIMITED),
        api_error(*UNAVAILABLE),
        openai.APITimeoutError(request=_REQUEST),
        openai.APIConnectionError(request=_REQUEST),
    ],
)
def test_temporary_error_is_retried_with_backoff(manager, backend, sleeps, error):
    backend.script["econ-a"] = [error, "recovered"]

    assert manager.call("hi", tier="economy") == ("recovered", "econ-a")
    assert sleeps == [2]


def test_sdk_retries_are_off_so_the_router_owns_retrying(manager, backend):
    backend.script["econ-a"] = ["ok"]
    manager.call("hi", tier="economy")

    assert backend.clients[0]["max_retries"] == 0


def test_disabled_provider_is_skipped_on_later_calls(manager, backend):
    backend.script["econ-a"] = [api_error(*NO_CREDITS)]
    backend.script["econ-b"] = ["first", "second"]

    manager.call("hi", tier="economy")
    manager.call("hi again", tier="economy")

    assert backend.models_called() == ["econ-a", "econ-b", "econ-b"]


def test_new_manager_starts_with_all_providers_active(manager, backend):
    backend.script["econ-a"] = [api_error(*NO_CREDITS)]
    manager.call("hi", tier="economy")

    fresh = llms.LLMProviderManager()
    assert all(p["is_active"] for p in fresh.providers)


# ── Request shape ────────────────────────────────────────────────────────────


def test_system_prompt_is_sent_before_user_prompt(manager, backend):
    backend.script["econ-a"] = ["ok"]
    manager.call("the question", tier="economy", system_prompt="be terse")

    assert backend.calls[0]["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "the question"},
    ]


def test_reasoning_flag_only_sent_to_providers_that_support_it(manager, backend):
    backend.script["econ-b"] = ["ok"]
    manager.call("hi", tier="economy", reasoning=False)

    econ_a_call, econ_b_call = backend.calls[0], backend.calls[-1]
    assert econ_a_call["extra_body"] == {"reasoning": {"enabled": False}}
    assert "extra_body" not in econ_b_call


# ── call_model ───────────────────────────────────────────────────────────────


def test_call_model_uses_configured_provider(manager, backend):
    backend.script["std-a"] = ["direct"]

    assert manager.call_model("std-a", "hi") == ("direct", "std-a")


def test_call_model_raises_instead_of_returning_none(manager):
    with pytest.raises(RuntimeError, match="std-a"):
        manager.call_model("std-a", "hi")


def test_call_model_unknown_model_goes_straight_to_openrouter_once(manager, backend, sleeps):
    with pytest.raises(RuntimeError):
        manager.call_model("some/unlisted-model", "hi")

    assert backend.models_called() == ["some/unlisted-model"]  # ad-hoc provider: 1 attempt
