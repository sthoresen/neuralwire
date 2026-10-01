# llms.py

import datetime
import os
import time
from typing import Any, Literal

import openai
from openai import OpenAI

import utils

Tier = Literal["economy", "standard", "premium"]

# (text, model_name) on success, (None, None) when every provider failed.
LLMReply = tuple[str, str] | tuple[None, None]

# How _try_provider reacts to a failed request. See classify_error().
ErrorAction = Literal["retry", "skip", "disable"]

# Tier escalation order (low → high cost/quality)
_TIER_ORDER: list[Tier] = ["economy", "standard", "premium"]


def classify_error(e: Exception) -> ErrorAction:
    """
    Decide what a failed request means, by HTTP status rather than message text.

    retry:   temporary (429, 5xx, timeout, connection error): back off and try again
    skip:    this request can't succeed on this provider (400, 422, ...): try the next one
    disable: this provider can't serve any request (402 no credits, 401/403 bad key,
             404 unknown or deprecated model, daily free quota): turn it off
    """
    if not isinstance(e, openai.APIStatusError):
        return "retry"  # timeouts, connection errors, anything unexpected

    # A 429 that won't clear until the daily quota resets, so retrying is pointless.
    if "free-models-per-day" in str(e) or "insufficient_quota" in str(e):
        return "disable"

    status = e.status_code
    if status in (401, 402, 403, 404):
        return "disable"
    if status in (408, 429) or status >= 500:
        return "retry"
    return "skip"


def _stream_grok_verbose(chat) -> str:
    """
    Streams a chat session, printing tool calls, reasoning progress, and
    citations as they arrive. Returns the full response content string.
    """
    import json

    content_parts = []
    is_thinking = True
    last_reasoning_tokens = 0
    response = None

    for response, chunk in chat.stream():
        for tool_call in chunk.tool_calls:
            args = tool_call.function.arguments
            try:
                args = json.dumps(json.loads(args), indent=2)
            except Exception:
                pass
            print(f"\n[x_search] {tool_call.function.name}({args})", flush=True)

        usage = getattr(response, "usage", None)
        if usage and getattr(usage, "reasoning_tokens", 0) != last_reasoning_tokens:
            last_reasoning_tokens = usage.reasoning_tokens
            print(f"\rThinking... ({last_reasoning_tokens} reasoning tokens)", end="", flush=True)

        if chunk.content and is_thinking:
            print("\n\n=== RESPONSE ===", flush=True)
            is_thinking = False

        if chunk.content:
            print(chunk.content, end="", flush=True)
            content_parts.append(chunk.content)

    if response is not None:
        citations = getattr(response, "citations", None)
        if citations:
            print("\n\n=== CITATIONS ===", flush=True)
            for c in citations:
                print(f"  {c}", flush=True)

        usage = getattr(response, "usage", None)
        if usage:
            print("\n\n=== USAGE ===", flush=True)
            print(f"  prompt_tokens:    {getattr(usage, 'prompt_tokens', '?')}", flush=True)
            print(f"  completion_tokens:{getattr(usage, 'completion_tokens', '?')}", flush=True)
            print(f"  reasoning_tokens: {getattr(usage, 'reasoning_tokens', '?')}", flush=True)

    print("\n", flush=True)
    return "".join(content_parts)


class LLMProviderManager:
    def __init__(self) -> None:
        # Keys are read here, not at import, so importing this module has no side effects.
        # Both required: get_env_variable raises if either is unset.
        self.openrouter_key = utils.get_env_variable("OPENROUTER_KEY")
        self.xai_key = utils.get_env_variable("XAI_API_KEY")

        self.providers: list[dict[str, Any]] = [
            # ── Economy ────────────────────────────────────────────────────────
            # Cheap, fast. For high-volume or low-stakes tasks (article triage,
            # color lookup, tag generation, event scan pass 1).
            {
                "name": "Tencent: Hy3",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": self.openrouter_key,
                "model": "tencent/hy3",
                "tier": "economy",
                "supports_reasoning": True,
                "is_active": True,
                "retries": 3,
            },
            {
                "name": "Gemma 4 31B IT",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": self.openrouter_key,
                "model": "google/gemma-4-31b-it",
                "tier": "economy",
                "supports_reasoning": True,
                "is_active": True,
                "retries": 3,
            },
            {
                "name": "OpenRouter (GPT-OSS 120B)",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": self.openrouter_key,
                "model": "openai/gpt-oss-120b:free",
                "tier": "economy",
                "supports_reasoning": False,
                "is_active": True,
                "retries": 3,
            },
            {
                "name": "OpenRouter (GPT-OSS 120B)",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": self.openrouter_key,
                "model": "openai/gpt-oss-120b",
                "tier": "economy",
                "supports_reasoning": False,
                "is_active": True,
                "retries": 3,
            },
            # ── Standard ───────────────────────────────────────────────────────
            # Good quality/cost balance. Default for most analysis and writing.
            {
                "name": "Tencent: Hy3",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": self.openrouter_key,
                "model": "tencent/hy3",
                "tier": "standard",
                "supports_reasoning": True,
                "is_active": True,
                "retries": 3,
            },
            {
                "name": "DeepSeek V4 Flash",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": self.openrouter_key,
                "model": "deepseek/deepseek-v4-flash",
                "tier": "standard",
                "supports_reasoning": False,
                "is_active": True,
                "retries": 3,
            },
            # ── Premium ────────────────────────────────────────────────────────
            # Highest quality. For complex synthesis or explicit override.
            {
                "name": "OpenRouter (Claude Haiku 4.5)",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": self.openrouter_key,
                "model": "anthropic/claude-haiku-4.5",
                "tier": "premium",
                "supports_reasoning": True,
                "is_active": True,
                "retries": 3,
            },
            {
                "name": "OpenRouter (Qwen 3.6 Plus)",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": self.openrouter_key,
                "model": "qwen/qwen3.6-plus",
                "tier": "premium",
                "supports_reasoning": True,
                "is_active": True,
                "retries": 3,
            },
        ]

        # xAI Direct is kept separate — used only by call_grok_with_search.
        # It requires the xAI SDK and is never eligible for call() routing.
        self.xai_config = {
            "name": "xAI Direct (Grok 4.3)",
            "api_key": self.xai_key,
            "model": "grok-4.3",
        }

    # ── Internal ───────────────────────────────────────────────────────────────

    def _try_provider(
        self,
        provider: dict[str, Any],
        prompt: str,
        system_prompt: str | None = None,
        reasoning: bool | None = None,
        max_tokens: int = 1000,
    ) -> LLMReply:
        """
        Attempt a single provider. Returns (text, name) or (None, None).

        reasoning: True/False to enable/disable the model's reasoning/thinking mode.
                   Silently ignored for providers that don't support it.
                   None = leave the provider's default intact.
        """
        name = provider["name"]
        if not provider.get("is_active"):
            print(f"  [{name}] skipped: not active", flush=True)
            return None, None
        if not provider.get("api_key") or "YOUR_" in (provider.get("api_key") or ""):
            print(f"  [{name}] skipped: no api key", flush=True)
            return None, None

        # max_retries=0: the SDK would otherwise retry 429/5xx itself before this loop
        # sees the error. One retry layer, the one below, that is logged and tested.
        client = OpenAI(base_url=provider["base_url"], api_key=provider["api_key"], max_retries=0)
        backoff = 2

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        kwargs = {
            "model": provider["model"],
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "extra_headers": {
                "HTTP-Referer": os.environ.get("FRONTEND_URL", "http://localhost:5000"),
                "X-Title": "Financial News Aggregator",
            }
            if "openrouter" in provider.get("base_url", "")
            else {},
        }

        if reasoning is not None and provider.get("supports_reasoning"):
            kwargs["extra_body"] = {"reasoning": {"enabled": reasoning}}

        retries = provider.get("retries", 1)
        last_failure = None

        for _ in range(retries):
            try:
                completion = client.chat.completions.create(**kwargs)
                if not completion.choices:
                    last_failure = "null/empty choices"
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                # Strip before the emptiness check: a reply of only whitespace is empty.
                content = (completion.choices[0].message.content or "").strip()
                if not content:
                    last_failure = "empty content (reasoning model hit token limit?)"
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                return content, name

            except Exception as e:
                action = classify_error(e)
                if action == "disable":
                    print(f"  [{name}] disabled: {str(e)[:120]}", flush=True)
                    provider["is_active"] = False
                    return None, None
                if action == "skip":
                    print(f"  [{name}] request rejected, trying next: {str(e)[:120]}", flush=True)
                    return None, None
                last_failure = str(e)[:120]
                time.sleep(backoff)
                backoff *= 2
                continue

        print(f"  [{name}] failed after {retries} attempt(s): {last_failure}", flush=True)
        return None, None

    # ── Public API ─────────────────────────────────────────────────────────────

    def call(
        self,
        prompt: str,
        tier: Tier = "standard",
        max_tier: Tier | None = None,
        reasoning: bool | None = None,
        max_tokens: int = 1000,
        system_prompt: str | None = None,
    ) -> LLMReply:
        """
        Route to providers by tier with optional escalation.

        tier:     Starting tier ("economy", "standard", "premium").
        max_tier: Highest tier allowed. None = no escalation (fail if tier fails).
        reasoning: True/False to control reasoning/thinking mode. None = model default.
        """
        effective_max = max_tier if max_tier is not None else tier

        start_idx = _TIER_ORDER.index(tier)
        end_idx = _TIER_ORDER.index(effective_max)

        for current_tier in _TIER_ORDER[start_idx : end_idx + 1]:
            candidates = [p for p in self.providers if p.get("tier") == current_tier]
            for provider in candidates:
                result, name = self._try_provider(
                    provider,
                    prompt,
                    system_prompt=system_prompt,
                    reasoning=reasoning,
                    max_tokens=max_tokens,
                )
                if result is not None:
                    return result, name

            if current_tier != _TIER_ORDER[end_idx]:
                print(
                    f"  [router] All {current_tier} providers failed, escalating to next tier...",
                    flush=True,
                )

        return None, None

    def call_model(
        self,
        model_id: str,
        prompt: str,
        reasoning: bool | None = None,
        max_tokens: int = 1000,
        system_prompt: str | None = None,
    ) -> tuple[str, str]:
        """
        Call a specific model by its ID, bypassing tier routing.
        If the model is in the configured provider list, uses that entry.
        Otherwise, calls it via OpenRouter directly.
        """
        provider = next((p for p in self.providers if p["model"] == model_id), None)

        if provider is None:
            provider = {
                "name": f"OpenRouter ({model_id})",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": self.openrouter_key,
                "model": model_id,
                "is_active": True,
                "supports_reasoning": True,  # pass through if caller requests it
                "retries": 1,
            }

        result, name = self._try_provider(
            provider,
            prompt,
            system_prompt=system_prompt,
            reasoning=reasoning,
            max_tokens=max_tokens,
        )
        if result is None or name is None:
            raise RuntimeError(f"call_model: '{model_id}' failed to produce a response.")
        return result, name

    def call_grok_with_search(
        self,
        prompt: str,
        print_reasoning: bool = False,
        allowed_x_handles: list[str] | None = None,
        excluded_x_handles: list[str] | None = None,
        from_date: datetime.datetime | None = None,
        to_date: datetime.datetime | None = None,
        enable_image_understanding: bool = False,
        enable_video_understanding: bool = False,
    ) -> tuple[str, str]:
        """
        Calls Grok 4.3 with x_search enabled using the xAI SDK.
        Supports advanced X search filtering, timing constraints, and multimodal understanding.
        """
        try:
            from xai_sdk import Client
            from xai_sdk.chat import user as xai_user
            from xai_sdk.tools import x_search
        except ImportError as e:
            raise RuntimeError("xai-sdk is not installed. Run: pip install xai-sdk") from e

        if allowed_x_handles and excluded_x_handles:
            raise ValueError("allowed_x_handles and excluded_x_handles cannot be set together.")

        now = datetime.datetime.now()
        full_prompt = (
            f"Today's date and time is {now.strftime('%A, %B %d, %Y at %I:%M %p')}.\n\n{prompt}"
        )

        search_kwargs = {}
        if allowed_x_handles:
            search_kwargs["allowed_x_handles"] = allowed_x_handles
        if excluded_x_handles:
            search_kwargs["excluded_x_handles"] = excluded_x_handles
        if from_date:
            search_kwargs["from_date"] = from_date
        if to_date:
            search_kwargs["to_date"] = to_date
        if enable_image_understanding:
            search_kwargs["enable_image_understanding"] = enable_image_understanding
        if enable_video_understanding:
            search_kwargs["enable_video_understanding"] = enable_video_understanding

        client = Client(api_key=self.xai_config["api_key"])
        chat = client.chat.create(
            model=self.xai_config["model"],
            tools=[x_search(**search_kwargs)],
            include=["verbose_streaming"],
        )
        chat.append(xai_user(full_prompt))

        try:
            if print_reasoning:
                content = _stream_grok_verbose(chat)
            else:
                response = chat.sample()
                content = response.content
        except Exception as e:
            raise RuntimeError(f"xAI SDK call failed: {e}") from e

        if not content:
            raise RuntimeError("xAI SDK returned empty content.")

        return content.strip(), self.xai_config["name"]
