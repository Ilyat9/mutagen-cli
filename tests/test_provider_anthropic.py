"""Offline tests for AnthropicProvider.

The `anthropic` SDK client is replaced with a small fake that records the
kwargs passed to `messages.create` and returns a canned response shaped like
the real Messages API — no network and no API key involved. Mirrors
test_provider_openrouter.py so the two first-class providers get matching
coverage.
"""

import json
from dataclasses import dataclass

import pytest

from mutagen_cli.cache import Cache
from mutagen_cli.prompts import MUTANT_SCHEMA
from mutagen_cli.provider import AnthropicProvider, ProviderError

PAYLOAD = {"mutants": [{"description": "cap becomes a floor",
                        "bug_category": "wrong_operator",
                        "search_block": "a = min(a, b)",
                        "replace_block": "a = max(a, b)"}]}


@dataclass
class FakeBlock:
    type: str
    text: str = ""


@dataclass
class FakeUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class FakeResponse:
    content: list
    usage: FakeUsage


class FakeMessages:
    def __init__(self, response=None, error=None, calls=None):
        self._response = response
        self._error = error
        self._calls = calls if calls is not None else []

    def create(self, **kwargs):
        self._calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


class FakeClient:
    def __init__(self, messages):
        self.messages = messages


def make_provider(response=None, error=None, calls=None, **kwargs):
    """AnthropicProvider wired to a fake client — no network, no SDK auth."""
    kwargs.setdefault("api_key", "sk-ant-test")
    provider = AnthropicProvider(**kwargs)
    if response is None and error is None:
        response = FakeResponse(
            content=[FakeBlock(type="text", text=json.dumps(PAYLOAD))],
            usage=FakeUsage(input_tokens=120, output_tokens=40),
        )
    provider._client = FakeClient(FakeMessages(response=response, error=error, calls=calls))
    return provider


def test_response_parses_into_reply():
    calls = []
    provider = make_provider(calls=calls)
    reply = provider.complete_json("sys", "user", MUTANT_SCHEMA)

    assert reply.data == PAYLOAD
    assert reply.input_tokens == 120
    assert reply.output_tokens == 40
    assert not reply.from_cache
    # claude-opus-5 default: $5/M in, $25/M out (table dated 2026-08-13)
    assert reply.cost_usd == pytest.approx((120 * 5.0 + 40 * 25.0) / 1_000_000)


def test_request_shape_sends_output_config_effort_and_schema():
    calls = []
    provider = make_provider(calls=calls, effort="high", max_tokens=8000)
    provider.complete_json("system prompt", "user prompt", MUTANT_SCHEMA)

    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["model"] == provider.model
    assert kwargs["max_tokens"] == 8000
    assert kwargs["system"] == "system prompt"
    assert kwargs["messages"] == [{"role": "user", "content": "user prompt"}]
    assert kwargs["output_config"] == {
        "effort": "high",
        "format": {"type": "json_schema", "schema": MUTANT_SCHEMA},
    }


def test_temperature_top_p_top_k_are_never_sent():
    # These sampling params are OpenRouter/Chat-Completions concepts; the
    # Anthropic structured-output path must not send them at all.
    calls = []
    provider = make_provider(calls=calls)
    provider.complete_json("sys", "user", MUTANT_SCHEMA)

    kwargs = calls[0]
    assert "temperature" not in kwargs
    assert "top_p" not in kwargs
    assert "top_k" not in kwargs


def test_reasoning_is_never_sent():
    # `reasoning` is OpenRouter's toggle; AnthropicProvider has no equivalent
    # request field, only the `effort` output_config knob.
    calls = []
    provider = make_provider(calls=calls)
    provider.complete_json("sys", "user", MUTANT_SCHEMA)

    kwargs = calls[0]
    assert "reasoning" not in kwargs
    assert "reasoning" not in kwargs.get("output_config", {})


def test_only_the_text_block_is_read():
    response = FakeResponse(
        content=[
            FakeBlock(type="thinking", text="pondering..."),
            FakeBlock(type="text", text=json.dumps(PAYLOAD)),
        ],
        usage=FakeUsage(input_tokens=10, output_tokens=5),
    )
    provider = make_provider(response=response)
    reply = provider.complete_json("sys", "user", MUTANT_SCHEMA)
    assert reply.data == PAYLOAD


def test_unparseable_json_raises_provider_error():
    response = FakeResponse(
        content=[FakeBlock(type="text", text="not json at all")],
        usage=FakeUsage(input_tokens=10, output_tokens=5),
    )
    provider = make_provider(response=response)
    with pytest.raises(ProviderError, match="unparseable JSON"):
        provider.complete_json("sys", "user", MUTANT_SCHEMA)


def test_missing_key_error_points_at_anthropic():
    provider = AnthropicProvider(api_key=None)
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        provider.complete_json("sys", "user", MUTANT_SCHEMA)


def test_price_overrides_from_config_apply():
    provider = make_provider(
        price_overrides={"someone/unpriced-model-9": [1.0, 2.0]},
        model="someone/unpriced-model-9",
    )
    reply = provider.complete_json("sys", "user", MUTANT_SCHEMA)
    assert reply.cost_usd == pytest.approx((120 * 1.0 + 40 * 2.0) / 1_000_000)


def test_unpriced_model_reports_no_cost_rather_than_zero():
    provider = make_provider(model="totally-unknown-model")
    reply = provider.complete_json("sys", "user", MUTANT_SCHEMA)
    assert reply.cost_usd is None


def test_cache_hit_skips_the_client_entirely(tmp_path):
    cache = Cache(tmp_path, enabled=True)
    calls = []
    provider = make_provider(calls=calls, cache=cache, model="shared-model-name")
    first = provider.complete_json("sys", "user", MUTANT_SCHEMA)
    assert not first.from_cache
    assert len(calls) == 1

    def boom(**kwargs):  # pragma: no cover - must not be called
        raise AssertionError("cache miss: the client should not have been called")

    provider._client.messages.create = boom
    second = provider.complete_json("sys", "user", MUTANT_SCHEMA)
    assert second.from_cache
    assert second.data == PAYLOAD


def test_cache_key_changes_with_effort():
    cache_key_medium = Cache.key(
        "anthropic", "m", "medium", "16000", "sys", "user",
        json.dumps(MUTANT_SCHEMA, sort_keys=True),
    )
    cache_key_high = Cache.key(
        "anthropic", "m", "high", "16000", "sys", "user",
        json.dumps(MUTANT_SCHEMA, sort_keys=True),
    )
    assert cache_key_medium != cache_key_high


def test_max_tokens_change_does_not_replay_a_truncated_cache_entry(tmp_path):
    cache = Cache(tmp_path, enabled=True)
    calls = []
    low = make_provider(calls=calls, cache=cache, max_tokens=100)
    assert not low.complete_json("sys", "user", MUTANT_SCHEMA).from_cache

    def boom(**kwargs):  # pragma: no cover - a hit here is the bug
        raise AssertionError("max_tokens=4000 reused the max_tokens=100 entry")

    high = make_provider(error=None, calls=None, cache=cache, max_tokens=4000)
    high._client.messages.create = boom
    with pytest.raises(AssertionError):
        high.complete_json("sys", "user", MUTANT_SCHEMA)
