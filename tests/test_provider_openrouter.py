"""Offline tests for OpenRouterProvider.

The HTTP layer is mocked with httpx.MockTransport, so no network and no API
key are involved. The fixtures mimic real OpenRouter Chat Completions
responses, including the reasoning fields the Claude 5 models emit when
reasoning is left on.
"""

import json

import httpx
import pytest

from mutagen_cli.cache import Cache
from mutagen_cli.prompts import MUTANT_SCHEMA
from mutagen_cli.provider import (
    AnthropicProvider,
    OpenRouterProvider,
    ProviderError,
    extract_json,
    resolve_api_key,
)

PAYLOAD = {"mutants": [{"description": "cap becomes a floor",
                        "bug_category": "wrong_operator",
                        "search_block": "a = min(a, b)",
                        "replace_block": "a = max(a, b)"}]}


def openai_response(content, prompt_tokens=120, completion_tokens=40, **message_extra):
    """Build an OpenAI-format chat completion body."""
    message = {"role": "assistant", "content": content, **message_extra}
    return {
        "id": "gen-123",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def make_provider(handler, **kwargs):
    kwargs.setdefault("api_key", "sk-or-test")
    kwargs.setdefault("transport", httpx.MockTransport(handler))
    return OpenRouterProvider(**kwargs)


def ok_handler(request):
    return httpx.Response(200, json=openai_response(json.dumps(PAYLOAD)))


def test_openai_format_response_parses_into_reply():
    provider = make_provider(ok_handler)
    reply = provider.complete_json("sys", "user", MUTANT_SCHEMA)

    assert reply.data == PAYLOAD
    assert reply.input_tokens == 120
    assert reply.output_tokens == 40
    assert not reply.from_cache
    # anthropic/claude-sonnet-5: $2/M in, $10/M out (table dated 2026-08-13)
    assert reply.cost_usd == pytest.approx((120 * 2.0 + 40 * 10.0) / 1_000_000)


def test_request_disables_reasoning_and_never_sends_sampling():
    seen = {}

    def handler(request):
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json=openai_response(json.dumps(PAYLOAD)))

    provider = make_provider(handler)
    provider.complete_json("sys", "user", MUTANT_SCHEMA)

    payload = seen["payload"]
    # Reasoning is on by default for the Claude 5 models on OpenRouter and
    # breaks JSON parsing; every request must switch it off explicitly.
    assert payload["reasoning"] == {"enabled": False}
    # Silently ignored by Sonnet 5 — must not be sent at all.
    assert "temperature" not in payload
    assert "top_p" not in payload
    assert "top_k" not in payload
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["messages"][0] == {"role": "system", "content": "sys"}


def test_reasoning_can_be_opted_back_in():
    seen = {}

    def handler(request):
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json=openai_response(json.dumps(PAYLOAD)))

    provider = make_provider(handler, reasoning=True)
    provider.complete_json("sys", "user", MUTANT_SCHEMA)
    assert "reasoning" not in seen["payload"]


def test_reasoning_blocks_in_the_reply_do_not_break_parsing():
    """A reply that still carries reasoning must not fail the parse."""
    content = (
        "Let me think about which mutants to write...\n"
        "The tests check the happy path only.\n"
        "```json\n" + json.dumps(PAYLOAD) + "\n```"
    )

    def handler(request):
        body = openai_response(
            content,
            reasoning="full reasoning trace the API kept separate",
            reasoning_details=[{"type": "reasoning.text", "text": "..."}],
        )
        return httpx.Response(200, json=body)

    provider = make_provider(handler)
    reply = provider.complete_json("sys", "user", MUTANT_SCHEMA)
    assert reply.data == PAYLOAD


def test_unsupported_response_format_retries_without_it():
    """Models without structured outputs get a 400; retry in plain JSON mode."""
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append("response_format" in payload)
        if "response_format" in payload:
            return httpx.Response(
                400, json={"error": {"message": "response_format not supported"}}
            )
        return httpx.Response(200, json=openai_response(json.dumps(PAYLOAD)))

    provider = make_provider(handler)
    reply = provider.complete_json("sys", "user", MUTANT_SCHEMA)

    assert calls == [True, False]
    assert reply.data == PAYLOAD


def test_unknown_model_reports_cost_as_unavailable():
    provider = make_provider(ok_handler, model="someone/unpriced-model-9")
    reply = provider.complete_json("sys", "user", MUTANT_SCHEMA)
    assert reply.cost_usd is None


def test_price_overrides_from_config_apply():
    provider = make_provider(
        ok_handler, price_overrides={"someone/unpriced-model-9": [1.0, 2.0]},
        model="someone/unpriced-model-9",
    )
    reply = provider.complete_json("sys", "user", MUTANT_SCHEMA)
    assert reply.cost_usd == pytest.approx((120 * 1.0 + 40 * 2.0) / 1_000_000)


def test_missing_key_error_points_at_openrouter():
    provider = OpenRouterProvider(api_key=None)
    with pytest.raises(ProviderError, match="openrouter.ai/keys"):
        provider.complete_json("sys", "user", MUTANT_SCHEMA)


def test_http_error_is_a_provider_error():
    def handler(request):
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    provider = make_provider(handler)
    with pytest.raises(ProviderError, match="OpenRouter request failed"):
        provider.complete_json("sys", "user", MUTANT_SCHEMA)


def test_cache_key_includes_the_provider(tmp_path):
    """Identical prompts to two providers must not share a cache entry."""
    cache = Cache(tmp_path, enabled=True)

    def handler(request):
        return httpx.Response(200, json=openai_response(json.dumps(PAYLOAD)))

    provider = make_provider(handler, cache=cache, model="shared-model-name")
    first = provider.complete_json("sys", "user", MUTANT_SCHEMA)
    assert not first.from_cache

    # Same model id on the Anthropic side must miss the OpenRouter entry.
    anthropic = AnthropicProvider(model="shared-model-name", api_key="sk-ant", cache=cache)
    assert anthropic.cache.get(
        Cache.key("anthropic", "shared-model-name", "medium", "sys", "user",
                  json.dumps(MUTANT_SCHEMA, sort_keys=True))
    ) is None

    # And a second OpenRouter call with the same model does hit the cache.
    def boom(request):  # pragma: no cover - must not be called
        raise AssertionError("cache miss: HTTP should not happen")

    cached = make_provider(boom, cache=cache, model="shared-model-name")
    second = cached.complete_json("sys", "user", MUTANT_SCHEMA)
    assert second.from_cache
    assert second.data == PAYLOAD


def test_reasoning_toggle_does_not_share_a_cache_entry(tmp_path):
    # Reasoning on and off produce different replies for the same prompt. If
    # the toggle is not in the key, flipping the config silently replays the
    # other mode's answer.
    cache = Cache(tmp_path, enabled=True)
    off = make_provider(ok_handler, cache=cache, reasoning=False)
    assert not off.complete_json("sys", "user", MUTANT_SCHEMA).from_cache
    assert off.complete_json("sys", "user", MUTANT_SCHEMA).from_cache

    def boom(request):  # pragma: no cover - a hit here is the bug
        raise AssertionError("reasoning=True reused the reasoning=False entry")

    on = make_provider(boom, cache=cache, reasoning=True)
    with pytest.raises(AssertionError):
        on.complete_json("sys", "user", MUTANT_SCHEMA)


def test_extract_json_tolerates_prose_and_fences():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Here is the answer:\n{"a": 1}\nHope that helps') == {"a": 1}
    with pytest.raises(ProviderError):
        extract_json("no json at all")


def test_extract_json_rejects_a_wrong_shaped_object_instead_of_going_silent():
    # A prose reply whose first balanced {...} is not the real payload (e.g. a
    # model musing "I'll check {status: ...}" before the answer) must not be
    # handed back as if it were valid — the caller's `.get("mutants", [])`
    # would see no such key, silently get [], and generation would end with
    # zero mutants and no error at all.
    with pytest.raises(ProviderError):
        extract_json('{"status": "thinking"}', required=["mutants"])
    # A later, correctly-shaped object in the same text is still found — only
    # a reply with no valid object anywhere is an error.
    assert extract_json(
        'musing {"status": "thinking"} then {"mutants": []}',
        required=["mutants"],
    ) == {"mutants": []}
    assert extract_json('{"mutants": []}', required=["mutants"]) == {"mutants": []}


def test_openrouter_provider_raises_on_reply_missing_the_mutants_key():
    def handler(request):
        return httpx.Response(200, json=openai_response('{"status": "thinking"}'))

    provider = make_provider(handler)
    with pytest.raises(ProviderError):
        provider.complete_json("sys", "user", MUTANT_SCHEMA)


def test_resolve_api_key_prefers_env_then_config(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    assert resolve_api_key("openrouter", tmp_path) == "sk-or-env"

    monkeypatch.delenv("OPENROUTER_API_KEY")
    config_dir = tmp_path / ".mutagen"
    config_dir.mkdir()
    (config_dir / "config.json").write_text('{"openrouter_api_key": "sk-or-file"}')
    assert resolve_api_key("openrouter", tmp_path) == "sk-or-file"

    # The legacy "api_key" still means Anthropic, never OpenRouter.
    (config_dir / "config.json").write_text('{"api_key": "sk-ant-legacy"}')
    assert resolve_api_key("openrouter", tmp_path) is None
    assert resolve_api_key("anthropic", tmp_path) == "sk-ant-legacy"
