"""Model transport.

The chat loop is the one place a provider swap can break silently: a wrong tool
schema or a mis-shaped tool result does not raise, it just makes the model stop
calling tools and start guessing -- which is exactly the failure this app is
built to not have. These tests pin the wire shapes without touching a network.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from fitness_ledger import chat, llm
from fitness_ledger.config import Config


def config(**overrides) -> Config:
    base = Config(
        db_path="unused",
        hevy_command="x", hevy_args=[], hevy_env={},
        health_command="x", health_args=[], health_env={},
    )
    return replace(base, **overrides)


# --- tool schema translation ----------------------------------------------


def test_conversion_moves_the_envelope_and_keeps_the_schema():
    converted = llm.to_openai_tools(
        [{
            "name": "get_volume",
            "description": "Effective sets.",
            "input_schema": {
                "type": "object",
                "properties": {"window": {"type": "string"}},
                "required": ["window"],
            },
        }]
    )

    assert converted == [{
        "type": "function",
        "function": {
            "name": "get_volume",
            "description": "Effective sets.",
            "parameters": {
                "type": "object",
                "properties": {"window": {"type": "string"}},
                "required": ["window"],
            },
        },
    }]


def test_argument_less_tools_still_declare_type_and_properties():
    # get_vitals and list_exercises take no arguments. Several providers reject a
    # function whose parameters omit either key, which would drop two tools
    # rather than fail loudly.
    converted = llm.to_openai_tools([{"name": "get_vitals", "description": "x", "input_schema": {}}])
    params = converted[0]["function"]["parameters"]

    assert params["type"] == "object"
    assert params["properties"] == {}


def test_conversion_does_not_mutate_the_declared_tools():
    original = {"name": "t", "description": "d", "input_schema": {}}
    llm.to_openai_tools([original])
    assert original["input_schema"] == {}


def test_every_declared_tool_converts():
    """The contract with every non-Anthropic provider: all 13 tools survive."""
    converted = llm.to_openai_tools(chat.TOOLS)

    assert len(converted) == len(chat.TOOLS)
    for entry in converted:
        function = entry["function"]
        assert entry["type"] == "function"
        assert function["name"] and function["description"]
        assert function["parameters"]["type"] == "object"
        assert isinstance(function["parameters"]["properties"], dict)


# --- provider resolution ---------------------------------------------------


def test_no_key_means_no_provider():
    assert llm.resolve_provider(config()) == "none"


def test_free_provider_is_preferred_when_both_keys_exist():
    resolved = llm.resolve_provider(config(gemini_api_key="g", anthropic_api_key="a"))
    assert resolved == "gemini"


def test_explicit_provider_beats_auto_detection():
    resolved = llm.resolve_provider(
        config(llm_provider="anthropic", gemini_api_key="g", anthropic_api_key="a")
    )
    assert resolved == "anthropic"


def test_llm_model_overrides_every_provider_default():
    assert llm.model_name(config(gemini_api_key="g")) == "gemini-3.6-flash"
    assert llm.model_name(config(gemini_api_key="g", llm_model="gemini-3.5-flash")) == "gemini-3.5-flash"
    assert llm.model_name(config(llm_provider="ollama")) == "qwen3:4b"


def test_unconfigured_build_names_the_free_option():
    # This string is what the chat dock shows the user, so it has to be actionable.
    with pytest.raises(llm.ProviderError) as caught:
        llm.build(config(), "system", chat.TOOLS)

    assert "GEMINI_API_KEY" in str(caught.value)


def test_gemini_without_a_key_is_an_explicit_error_not_a_fallback():
    with pytest.raises(llm.ProviderError, match="GEMINI_API_KEY"):
        llm.build(config(llm_provider="gemini"), "system", chat.TOOLS)


def test_unknown_provider_is_rejected():
    with pytest.raises(llm.ProviderError, match="Unknown LLM_PROVIDER"):
        llm.build(config(llm_provider="hal9000"), "system", chat.TOOLS)


# --- OpenAI-compatible wire format -----------------------------------------


def fake_reply(content=None, tool_calls=(), finish_reason="stop"):
    """Enough of the SDK response object for the transport to read."""
    message = SimpleNamespace(
        content=content,
        tool_calls=[
            SimpleNamespace(
                id=call_id,
                function=SimpleNamespace(name=name, arguments=arguments),
            )
            for call_id, name, arguments in tool_calls
        ],
        model_dump=lambda exclude_none=False: {"role": "assistant", "content": content},
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)]
    )


def transport(reply, **kwargs) -> llm.OpenAICompatibleTransport:
    made = llm.OpenAICompatibleTransport(
        "test-model", "system prompt", chat.TOOLS,
        api_key="k", base_url="http://x/v1", **kwargs,
    )

    async def create(**kwargs):
        # Snapshot: the transport keeps appending to the same list after the
        # call returns, so holding the reference would show a later state.
        made.last_request = {**kwargs, "messages": list(kwargs["messages"])}
        return reply

    made._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return made


def test_tool_calls_are_normalised_with_parsed_arguments():
    made = transport(
        fake_reply(tool_calls=[("call_1", "get_volume", '{"window": "last-week"}')])
    )
    made.ask("how much chest?")
    turn = asyncio.run(made.turn())

    assert turn.tool_calls == [
        llm.ToolCall(id="call_1", name="get_volume", arguments={"window": "last-week"})
    ]


def test_a_reply_without_tool_calls_ends_the_loop():
    turn = asyncio.run(transport(fake_reply(content="  14 effective sets.  ")).turn())

    assert turn.text == "14 effective sets."
    assert turn.tool_calls == []


def test_the_system_prompt_leads_the_conversation():
    made = transport(fake_reply(content="hi"))
    made.ask("question")
    asyncio.run(made.turn())

    assert made.last_request["messages"][0] == {"role": "system", "content": "system prompt"}
    assert made.last_request["messages"][1] == {"role": "user", "content": "question"}


def test_tool_results_are_one_message_each_keyed_by_call_id():
    made = transport(fake_reply(tool_calls=[("call_1", "get_vitals", "{}")]))
    made.ask("vitals?")
    turn = asyncio.run(made.turn())
    made.record([(turn.tool_calls[0], json.dumps({"age": 28}))])
    asyncio.run(made.turn())

    assert made.last_request["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"age": 28}',
    }


# --- thinking budget and truncation ----------------------------------------
# Gemini 2.5 Flash reasons by default and those tokens come out of max_tokens
# without appearing in the reply: measured 554 thinking tokens on an 11-token
# prompt, which cut a real answer off mid-sentence. The model is forbidden from
# reasoning about numbers here, so thinking is pure cost and pure risk.


def test_gemini_asks_for_the_least_thinking_the_provider_allows():
    # Was "none" until 2026-08-29. Gemini 3.6 Flash rejects that value with a
    # 400 that names no parameter, which broke every Gemini request the app
    # made. This assertion previously pinned the broken value -- the same way
    # three tests once pinned the gemini-2.5-flash default that returned 404.
    assert llm._effort(config(gemini_api_key="g"), "gemini") == "minimal"


def test_providers_without_a_default_send_nothing():
    assert llm._effort(config(), "ollama") is None
    assert llm._effort(config(), "openai-compatible") is None


def test_effort_can_be_configured_back_on():
    assert llm._effort(config(llm_reasoning_effort="low"), "gemini") == "low"


def test_off_suppresses_the_parameter_for_providers_that_reject_it():
    assert llm._effort(config(llm_reasoning_effort="off"), "gemini") is None


def test_effort_is_sent_only_when_set():
    with_effort = transport(fake_reply(content="hi"), reasoning_effort="minimal")
    asyncio.run(with_effort.turn())
    assert with_effort.last_request["reasoning_effort"] == "minimal"

    without = transport(fake_reply(content="hi"))
    asyncio.run(without.turn())
    assert "reasoning_effort" not in without.last_request


def test_a_complete_reply_is_not_flagged_truncated():
    assert asyncio.run(transport(fake_reply(content="done")).turn()).truncated is False


def test_hitting_the_token_ceiling_is_reported_not_swallowed():
    turn = asyncio.run(
        transport(fake_reply(content="half a sen", finish_reason="length")).turn()
    )
    assert turn.truncated is True


def test_the_loop_states_the_limit_rather_than_trailing_off(monkeypatch):
    """A cut-off answer must not read as the model simply stopping."""

    class Stub:
        def ask(self, question): pass
        def record(self, results): pass
        async def turn(self):
            return llm.Turn(text="Your AEI is", truncated=True)

    monkeypatch.setattr(llm, "build", lambda *a, **k: Stub())
    reply = asyncio.run(chat.answer(None, config(), "how is my running?"))

    assert reply.startswith("Your AEI is")
    assert "token limit" in reply


def test_malformed_tool_arguments_degrade_to_empty():
    # A small local model can emit almost-JSON. An empty dict lets the tool
    # report a usable error instead of the whole request dying on a parse.
    assert llm._loads('{"window": "last-week"}') == {"window": "last-week"}
    assert llm._loads("{not json") == {}
    assert llm._loads("") == {}
    assert llm._loads(None) == {}
    assert llm._loads('["a list"]') == {}


# --- bounding a slow provider ----------------------------------------------
# Measured against gemini-3.6-flash on 2026-09-02: the same request five times
# took 2.9s, 15.0s, 26.5s, 103.7s and 6.4s. The SDK's own defaults are a 600
# second timeout and two silent retries, so the worst case behind a spinner is
# indistinguishable from a hang.


def test_a_timeout_and_retry_budget_are_set_by_default():
    limits = llm._limits(config())
    assert limits["timeout"] == 30.0
    assert limits["max_retries"] == 1


def test_a_zero_timeout_means_use_the_sdk_default():
    # The escape hatch for a deliberately slow local model: Ollama on CPU can
    # legitimately take minutes, and cutting it off is not a kindness.
    assert "timeout" not in llm._limits(config(llm_timeout_seconds=0))


def test_a_negative_retry_budget_is_clamped_rather_than_passed_through():
    assert llm._limits(config(llm_max_retries=-3))["max_retries"] == 0


def test_a_timeout_is_explained_rather_than_raised_raw():
    class APITimeoutError(Exception):
        pass

    message = llm.describe_provider_failure(APITimeoutError("Request timed out."))
    assert message and "did not answer in time" in message
    # It has to say what to change, not just what went wrong.
    assert "LLM_TIMEOUT_SECONDS" in message


def test_a_quota_refusal_is_explained():
    class RateLimitError(Exception):
        pass

    message = llm.describe_provider_failure(RateLimitError("Error code: 429 - quota"))
    assert message and "quota exhausted" in message


def test_an_unrecognised_failure_is_left_alone():
    # Dressing up an unknown fault as a friendly message loses the traceback
    # that would have explained it.
    assert llm.describe_provider_failure(ValueError("something else entirely")) is None


def test_provider_unavailable_is_still_a_runtime_error():
    # api.py catches RuntimeError to turn provider problems into a 503. An
    # exception outside that hierarchy surfaces as an opaque 500 instead --
    # which is exactly what a real 429 did before this was added.
    assert issubclass(llm.ProviderUnavailable, llm.ProviderError)
    assert issubclass(llm.ProviderUnavailable, RuntimeError)


# --- classifying a provider failure -----------------------------------------
# Matching a bare "429" anywhere in the message classified
# `Bad request: invalid argument (request id req_a429bf)` as a quota refusal.
# The dock told the user to change providers, and the coach -- which shares this
# judgement -- launched a paid fallback run to re-execute a plan that was going
# to fail for its own reasons.


class _Status(Exception):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


def test_a_status_code_decides_before_any_text_matching():
    assert llm.is_quota_refusal(_Status("slow down", 429)) is True
    # A 400 is a 400 even when its body happens to contain the digits.
    assert llm.is_quota_refusal(_Status("invalid arg req_a429bf", 400)) is False


def test_a_request_id_containing_429_is_not_a_quota_refusal():
    exc = RuntimeError("Bad request: invalid argument (request id req_a429bf)")
    assert llm.is_quota_refusal(exc) is False
    assert llm.describe_provider_failure(exc) is None


def test_a_real_quota_refusal_is_still_recognised():
    assert llm.is_quota_refusal(RuntimeError("Error code: 429 - too many requests"))
    assert llm.is_quota_refusal(RuntimeError("RESOURCE_EXHAUSTED"))
    assert llm.is_quota_refusal(RuntimeError("You exceeded your current quota"))


def test_the_coach_and_the_dock_cannot_disagree_about_a_quota_error():
    """They were two independent matchers and had already drifted.

    This one knew RESOURCE_EXHAUSTED and the dock's did not, so the same
    exception could be a quota problem for one and a real fault for the other.
    The coach's is the expensive disagreement: a false positive spends money.
    """
    from fitness_ledger.coach import is_quota_error

    for exc in (
        RuntimeError("RESOURCE_EXHAUSTED"),
        RuntimeError("Bad request (request id req_a429bf)"),
        _Status("rate limited", 429),
        _Status("bad request", 400),
    ):
        assert is_quota_error(exc) is llm.is_quota_refusal(exc)


def test_a_rejected_key_is_explained_rather_than_raised_raw():
    """Gemini's shim answers a bad key with 400 'Please pass a valid API key',
    not a 401, so matching only AuthenticationError left the commonest setup
    mistake in this app surfacing as an unexplained 500."""
    message = llm.describe_provider_failure(
        _Status("Error code: 400 - Please pass a valid API key", 400)
    )
    assert message and "credentials" in message
    assert "LLM_PROVIDER" in message


def test_a_retired_model_id_is_explained():
    class NotFoundError(Exception):
        pass

    message = llm.describe_provider_failure(NotFoundError("model not found"))
    assert message and "does not recognise that model" in message
