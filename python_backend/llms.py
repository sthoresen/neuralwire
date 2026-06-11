#llms.py

import os

from openai import OpenAI
import time
import datetime

import utils

OPENROUTER_KEY = utils.get_env_variable('OPENROUTER_KEY')
XAI_API_KEY    = utils.get_env_variable('XAI_API_KEY')

# Tier escalation order (low → high cost/quality)
_TIER_ORDER = ["economy", "standard", "premium"]


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

        usage = getattr(response, 'usage', None)
        if usage and getattr(usage, 'reasoning_tokens', 0) != last_reasoning_tokens:
            last_reasoning_tokens = usage.reasoning_tokens
            print(f"\rThinking... ({last_reasoning_tokens} reasoning tokens)", end="", flush=True)

        if chunk.content and is_thinking:
            print(f"\n\n=== RESPONSE ===", flush=True)
            is_thinking = False

        if chunk.content:
            print(chunk.content, end="", flush=True)
            content_parts.append(chunk.content)

    if response is not None:
        citations = getattr(response, 'citations', None)
        if citations:
            print(f"\n\n=== CITATIONS ===", flush=True)
            for c in citations:
                print(f"  {c}", flush=True)

        usage = getattr(response, 'usage', None)
        if usage:
            print(f"\n\n=== USAGE ===", flush=True)
            print(f"  prompt_tokens:    {getattr(usage, 'prompt_tokens', '?')}", flush=True)
            print(f"  completion_tokens:{getattr(usage, 'completion_tokens', '?')}", flush=True)
            print(f"  reasoning_tokens: {getattr(usage, 'reasoning_tokens', '?')}", flush=True)

    print("\n", flush=True)
    return "".join(content_parts)


class LLMProviderManager:
    def __init__(self):
        self.providers = [
            # ── Economy ────────────────────────────────────────────────────────
            # Cheap, fast. For high-volume or low-stakes tasks (article triage,
            # color lookup, tag generation, event scan pass 1).
            {
                "name": "Tencent: Hy3 preview",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": OPENROUTER_KEY,
                "model": "tencent/hy3-preview",
                "tier": "economy",
                "supports_reasoning": True,
                "is_active": True,
                "retries": 3,
            },
            {
                "name": "Gemma 4 31B IT",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": OPENROUTER_KEY,
                "model": "google/gemma-4-31b-it",
                "tier": "economy",
                "supports_reasoning": True,
                "is_active": True,
                "retries": 3,
            },
            {
                "name": "OpenRouter (GPT-OSS 120B)",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": OPENROUTER_KEY,
                "model": "openai/gpt-oss-120b:free",
                "tier": "economy",
                "supports_reasoning": False,
                "is_active": True,
                "retries": 3,
            },
            {
                "name": "OpenRouter (GPT-OSS 120B)",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": OPENROUTER_KEY,
                "model": "openai/gpt-oss-120b",
                "tier": "economy",
                "supports_reasoning": False,
                "is_active": True,
                "retries": 3,
            },


            # ── Standard ───────────────────────────────────────────────────────
            # Good quality/cost balance. Default for most analysis and writing.
            {
                "name": "Tencent: Hy3 preview",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": OPENROUTER_KEY,
                "model": "tencent/hy3-preview",
                "tier": "standard",
                "supports_reasoning": True,
                "is_active": True,
                "retries": 3,
            },
            {
                "name": "DeepSeek V4 Flash",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": OPENROUTER_KEY,
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
                "api_key": OPENROUTER_KEY,
                "model": "anthropic/claude-haiku-4.5",
                "tier": "premium",
                "supports_reasoning": True,
                "is_active": True,
                "retries": 3,
            },
            {
                "name": "OpenRouter (Qwen 3.6 Plus)",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": OPENROUTER_KEY,
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
            "name": "xAI Direct (Grok 4.1 Fast)",
            "api_key": XAI_API_KEY,
            "model": "grok-4-1-fast-reasoning",
            "active": bool(XAI_API_KEY),
        }

    # ── Internal ───────────────────────────────────────────────────────────────

    def _try_provider(self, provider, prompt, system_prompt=None, reasoning=None, max_tokens=1000):
        """
        Attempt a single provider. Returns (text, name) or (None, None).

        reasoning: True/False to enable/disable the model's reasoning/thinking mode.
                   Silently ignored for providers that don't support it.
                   None = leave the provider's default intact.
        """
        name = provider['name']
        if not provider.get("is_active"):
            print(f'  [{name}] skipped: not active', flush=True)
            return None, None
        if not provider.get("api_key") or "YOUR_" in (provider.get("api_key") or ""):
            print(f'  [{name}] skipped: no api key', flush=True)
            return None, None

        client  = OpenAI(base_url=provider['base_url'], api_key=provider['api_key'])
        backoff = 2

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        kwargs = {
            "model":      provider['model'],
            "messages":   messages,
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "extra_headers": {
                "HTTP-Referer": os.environ.get("FRONTEND_URL", "http://localhost:5000"),
                "X-Title": "Financial News Aggregator",
            } if "openrouter" in provider.get('base_url', '') else {},
        }

        if reasoning is not None and provider.get("supports_reasoning"):
            kwargs["extra_body"] = {"reasoning": {"enabled": reasoning}}

        retries = provider.get('retries', 1)
        last_failure = None

        for attempt in range(retries):
            try:
                completion = client.chat.completions.create(**kwargs)
                if not completion.choices:
                    last_failure = "null/empty choices"
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                content = completion.choices[0].message.content
                if not content:
                    last_failure = "empty content (reasoning model hit token limit?)"
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                return content.strip(), name

            except Exception as e:
                error_msg = str(e)
                if "free-models-per-day" in error_msg or "insufficient_quota" in error_msg:
                    print(f"  [{name}] daily limit reached. Disabling.", flush=True)
                    provider["is_active"] = False
                    return None, None
                if "429" in error_msg:
                    last_failure = "rate-limited (429)"
                else:
                    last_failure = error_msg[:120]
                time.sleep(backoff)
                backoff *= 2
                continue

        print(f'  [{name}] failed after {retries} attempt(s): {last_failure}', flush=True)
        return None, None

    # ── Public API ─────────────────────────────────────────────────────────────

    def call(self, prompt, tier="standard", max_tier=None, reasoning=None,
             max_tokens=1000, system_prompt=None):
        """
        Route to providers by tier with optional escalation.

        tier:     Starting tier ("economy", "standard", "premium").
        max_tier: Highest tier allowed. None = no escalation (fail if tier fails).
        reasoning: True/False to control reasoning/thinking mode. None = model default.
        """
        effective_max = max_tier if max_tier is not None else tier

        start_idx = _TIER_ORDER.index(tier)
        end_idx   = _TIER_ORDER.index(effective_max)

        for current_tier in _TIER_ORDER[start_idx:end_idx + 1]:
            candidates = [p for p in self.providers if p.get("tier") == current_tier]
            for provider in candidates:
                result, name = self._try_provider(
                    provider, prompt,
                    system_prompt=system_prompt,
                    reasoning=reasoning,
                    max_tokens=max_tokens,
                )
                if result is not None:
                    return result, name

            if current_tier != _TIER_ORDER[end_idx]:
                print(f"  [router] All {current_tier} providers failed, escalating to next tier...", flush=True)

        return None, None

    def call_model(self, model_id, prompt, reasoning=None, max_tokens=1000, system_prompt=None):
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
                "api_key": OPENROUTER_KEY,
                "model": model_id,
                "is_active": True,
                "supports_reasoning": True,  # pass through if caller requests it
                "retries": 1,
            }

        result, name = self._try_provider(
            provider, prompt,
            system_prompt=system_prompt,
            reasoning=reasoning,
            max_tokens=max_tokens,
        )
        if result is None:
            raise RuntimeError(f"call_model: '{model_id}' failed to produce a response.")
        return result, name

    def call_grok_with_search(self, prompt, print_reasoning=False):
        """
        Calls Grok with x_search enabled using the xAI SDK.
        This path is intentionally separate — x_search requires the xAI SDK's
        internal agentic loop and cannot be routed through call().
        """
        if not self.xai_config.get("active"):
            raise RuntimeError(
                "xAI Direct provider not configured. Add XAI_API_KEY to .env."
            )

        try:
            from xai_sdk import Client
            from xai_sdk.chat import user as xai_user
            from xai_sdk.tools import x_search
        except ImportError:
            raise RuntimeError("xai-sdk is not installed. Run: pip install xai-sdk")

        now = datetime.datetime.now()
        full_prompt = f"Today's date and time is {now.strftime('%A, %B %d, %Y at %I:%M %p')}.\n\n{prompt}"

        client = Client(api_key=self.xai_config['api_key'])
        chat   = client.chat.create(
            model=self.xai_config['model'],
            tools=[x_search()],
            include=["verbose_streaming"],
        )
        chat.append(xai_user(full_prompt))

        try:
            if print_reasoning:
                content = _stream_grok_verbose(chat)
            else:
                response = chat.sample()
                content  = response.content
        except Exception as e:
            raise RuntimeError(f"xAI SDK call failed: {e}")

        if not content:
            raise RuntimeError("xAI SDK returned empty content.")

        return content.strip(), self.xai_config['name']


# Module-level singleton for callers that import it directly
llm_manager = LLMProviderManager()
