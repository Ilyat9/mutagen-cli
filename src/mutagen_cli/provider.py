"""LLM transport.

One small surface — `complete_json` — so adding OpenAI or Ollama later means
adding a class here and nothing else.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

from .cache import Cache

# USD per million tokens (input, output). Anthropic entries are list prices;
# the OpenRouter entries were taken from openrouter.ai on 2026-08-13. Override
# any of them with a "prices" table in the config file (see load_config).
PRICES = {
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
    # OpenRouter prices, 2026-08-13.
    "anthropic/claude-sonnet-5": (2.0, 10.0),
    "anthropic/claude-opus-5": (5.0, 25.0),
}

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
DEFAULT_OPENROUTER_MODEL = "anthropic/claude-sonnet-5"
DEFAULT_MODEL = DEFAULT_OPENROUTER_MODEL
DEFAULT_PROVIDER = "openrouter"

ENV_KEYS = {"anthropic": "ANTHROPIC_API_KEY", "openrouter": "OPENROUTER_API_KEY"}


def reasoning_tag(reasoning: bool) -> str:
    """Cache-key fragment for the OpenRouter reasoning toggle."""
    return "reasoning=on" if reasoning else "reasoning=off"


def default_model(provider: str) -> str:
    return DEFAULT_OPENROUTER_MODEL if provider == "openrouter" else DEFAULT_ANTHROPIC_MODEL


def price_of(
    model: str,
    input_tokens: int,
    output_tokens: int,
    overrides: Optional[dict] = None,
) -> Optional[float]:
    """Cost of one call in USD, or None when the model has no known price.

    None is deliberate: reporting $0 for an unpriced model would be a lie, so
    the report renders "cost unavailable" instead.
    """
    table = dict(PRICES)
    for key, value in (overrides or {}).items():
        try:
            inp, out = float(value[0]), float(value[1])
        except (TypeError, IndexError, ValueError):
            continue
        table[key] = (inp, out)
    if model not in table:
        return None
    inp, out = table[model]
    return (input_tokens * inp + output_tokens * out) / 1_000_000


@dataclass
class Reply:
    data: object  # parsed JSON
    input_tokens: int = 0
    output_tokens: int = 0
    from_cache: bool = False
    cost_usd: Optional[float] = None  # None = price unknown, not zero


class ProviderError(RuntimeError):
    pass


def _config_candidates(repo_root: Optional[Path]) -> list[Path]:
    candidates = []
    if repo_root:
        candidates.append(Path(repo_root) / ".mutagen" / "config.json")
    candidates.append(Path.home() / ".config" / "mutagen" / "config.json")
    return candidates


def load_config(repo_root: Optional[Path] = None) -> dict:
    """First readable config file wins. Shape (all keys optional):

        {
          "api_key": "...",             # legacy, treated as the Anthropic key
          "anthropic_api_key": "...",
          "openrouter_api_key": "...",
          "openrouter_reasoning": false,
          "prices": {"model/id": [input_per_mtok, output_per_mtok]}
        }
    """
    for path in _config_candidates(repo_root):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def resolve_api_key(provider: str, repo_root: Optional[Path] = None) -> Optional[str]:
    """Env var first, then a config file, so a key can live outside the shell."""
    key = os.environ.get(ENV_KEYS.get(provider, ""))
    if key:
        return key
    config = load_config(repo_root)
    if provider == "openrouter":
        return config.get("openrouter_api_key")
    # "api_key" is the pre-multi-provider name and still means Anthropic.
    return config.get("anthropic_api_key") or config.get("api_key")


def missing_key_error(provider: str) -> str:
    if provider == "openrouter":
        return (
            "no OpenRouter API key found. Get one at https://openrouter.ai/keys "
            "(works from Russia without a VPN), then set OPENROUTER_API_KEY or put "
            '{"openrouter_api_key": "..."} in .mutagen/config.json'
        )
    return (
        "no API key found. Set ANTHROPIC_API_KEY, or put "
        '{"anthropic_api_key": "..."} in .mutagen/config.json'
    )


def extract_json(text: str) -> object:
    """Parse JSON out of a model reply that may not be bare JSON.

    Tolerates markdown fences and prose around the payload — models without
    schema-constrained output sometimes prepend reasoning or wrap the answer.
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the first balanced JSON object in the text.
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char == "{":
            try:
                data, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            return data
    raise ProviderError("model returned unparseable JSON")


class AnthropicProvider:
    """Calls the Messages API and returns schema-validated JSON.

    Uses structured outputs (`output_config.format`) rather than asking for
    JSON in prose and parsing a fenced block — the API guarantees the shape,
    which removes a whole class of retry logic.
    """

    provider_name = "anthropic"

    def __init__(
        self,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        api_key: Optional[str] = None,
        cache: Optional[Cache] = None,
        effort: str = "medium",
        max_tokens: int = 16000,
        price_overrides: Optional[dict] = None,
    ):
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.cache = cache
        self.price_overrides = price_overrides
        self._api_key = api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - dependency guard
                raise ProviderError(
                    "the 'anthropic' package is required: pip install anthropic"
                ) from exc
            if not self._api_key:
                raise ProviderError(missing_key_error("anthropic"))
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def complete_json(self, system: str, user: str, schema: dict) -> Reply:
        # The provider name is part of the cache key: identical prompts to two
        # providers must not share an entry.
        cache_key = Cache.key(
            self.provider_name, self.model, self.effort, system, user,
            json.dumps(schema, sort_keys=True),
        )
        if self.cache:
            hit = self.cache.get(cache_key)
            if hit is not None:
                return Reply(data=hit["data"], from_cache=True)

        client = self._get_client()
        response = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
        )

        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"model returned unparseable JSON: {exc}") from exc

        if self.cache:
            self.cache.put(cache_key, {"data": data})

        return Reply(
            data=data,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost_usd=price_of(
                self.model, response.usage.input_tokens,
                response.usage.output_tokens, self.price_overrides,
            ),
        )


class OpenRouterProvider:
    """Calls OpenRouter's OpenAI-compatible Chat Completions API.

    Plain httpx rather than the openai SDK: the SDK pulls in a large tree for
    what is one POST here, and httpx was already on disk via anthropic.
    See DECISIONS.md D9.

    Model quirks (checked 2026-08-13, see
    https://openrouter.ai/docs/use-cases/reasoning-tokens and the migration
    guides under https://openrouter.ai/docs/guides):
    - anthropic/claude-sonnet-5 and anthropic/claude-opus-5 run with reasoning
      ENABLED by default. Reasoning blocks pollute the reply and inflate cost
      and latency, so every request sends reasoning={"enabled": false}. Set
      "openrouter_reasoning": true in the config to opt back in.
    - temperature / top_p / top_k are silently ignored by the Claude 5 models
      on OpenRouter, so this provider never sends them.
    """

    provider_name = "openrouter"
    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(
        self,
        model: str = DEFAULT_OPENROUTER_MODEL,
        api_key: Optional[str] = None,
        cache: Optional[Cache] = None,
        max_tokens: int = 16000,
        reasoning: bool = False,
        price_overrides: Optional[dict] = None,
        transport=None,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.cache = cache
        self.reasoning = reasoning
        self.price_overrides = price_overrides
        self._api_key = api_key
        self._transport = transport
        self._client = None

    def _get_client(self):
        if self._client is None:
            if not self._api_key:
                raise ProviderError(missing_key_error("openrouter"))
            self._client = httpx.Client(
                base_url=self.BASE_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=httpx.Timeout(300.0, connect=30.0),
                transport=self._transport,
            )
        return self._client

    def _payload(self, system: str, user: str, schema: dict, structured: bool) -> dict:
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if structured:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "mutagen_reply",
                    "strict": True,
                    "schema": schema,
                },
            }
        if not self.reasoning:
            payload["reasoning"] = {"enabled": False}
        return payload

    def complete_json(self, system: str, user: str, schema: dict) -> Reply:
        # The provider name is part of the cache key: identical prompts to two
        # providers must not share an entry. So is the reasoning toggle — it
        # changes the reply, and it is the OpenRouter analogue of `effort`.
        cache_key = Cache.key(
            self.provider_name, self.model, reasoning_tag(self.reasoning),
            system, user, json.dumps(schema, sort_keys=True),
        )
        if self.cache:
            hit = self.cache.get(cache_key)
            if hit is not None:
                return Reply(data=hit["data"], from_cache=True)

        client = self._get_client()
        try:
            response = client.post(
                "/chat/completions", json=self._payload(system, user, schema, True)
            )
            # Not every model routed through OpenRouter supports structured
            # outputs; on a 400 retry once without response_format and parse
            # the reply tolerantly instead.
            if response.status_code == 400:
                response = client.post(
                    "/chat/completions", json=self._payload(system, user, schema, False)
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"OpenRouter request failed: {exc}") from exc

        body = response.json()
        message = (body.get("choices") or [{}])[0].get("message") or {}
        # `reasoning` / `reasoning_details` fields, when present, are ignored —
        # only `content` carries the answer.
        data = extract_json(message.get("content") or "")

        if self.cache:
            self.cache.put(cache_key, {"data": data})

        usage = body.get("usage") or {}
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        return Reply(
            data=data,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=price_of(
                self.model, input_tokens, output_tokens, self.price_overrides
            ),
        )


class ReplayProvider:
    """Serves canned replies. Used by mutagen's own test suite so the tests
    run offline and for free."""

    provider_name = "replay"

    def __init__(self, replies: list):
        self._replies = list(replies)
        self.calls = 0

    def complete_json(self, system: str, user: str, schema: dict) -> Reply:
        self.calls += 1
        if not self._replies:
            raise ProviderError("ReplayProvider ran out of canned replies")
        return Reply(data=self._replies.pop(0))
