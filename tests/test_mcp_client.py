"""The MCP client's failure handling.

Two rules, and the second is the one with history behind it:

- A rate limit is worth asking again about. A full sync is roughly fifty calls,
  so one refusal used to abort a whole step and leave the cache half-populated.
- Nothing else is. Retrying a bad request only makes it a slow bad request, and
  a sync that half-completes while reporting success is issue #16.
"""

from __future__ import annotations

import asyncio

import pytest

from fitness_ledger import mcp_client
from fitness_ledger.mcp_client import MCPClient, MCPError, MCPRateLimited


class Block:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class Result:
    def __init__(self, text: str, is_error: bool = False) -> None:
        self.content = [Block(text)]
        self.isError = is_error


class Session:
    """Returns each queued result in turn, counting the calls."""

    def __init__(self, results: list[Result]) -> None:
        self._results = list(results)
        self.calls = 0

    async def call_tool(self, tool, arguments):
        self.calls += 1
        return self._results.pop(0) if self._results else self._results_exhausted()

    @staticmethod
    def _results_exhausted():
        raise AssertionError("called more times than the test queued results for")


@pytest.fixture(autouse=True)
def no_real_waiting(monkeypatch):
    """Backoff is real seconds. The test asserts it is *asked for*, not slept."""
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(mcp_client.asyncio, "sleep", fake_sleep)
    return slept


def client(session: Session) -> MCPClient:
    instance = MCPClient("x", [])
    instance._session = session
    return instance


def call(instance: MCPClient):
    return asyncio.run(instance.call("hevy_list_exercise_templates"))


# --- classification --------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Error: Hevy rate limit exceeded",
        "Error: 429 Too Many Requests",
        "Error: too many requests, slow down",
    ],
)
def test_a_rate_limit_is_recognised_however_it_is_worded(text):
    # Neither server sets a status code we can read, so this is matched on the
    # message. Hevy reports a limit as plain text.
    assert isinstance(mcp_client._failure("t", text), MCPRateLimited)


def test_a_real_fault_is_not_mistaken_for_a_rate_limit():
    failure = mcp_client._failure("t", "Error: invalid api key")
    assert isinstance(failure, MCPError)
    assert not isinstance(failure, MCPRateLimited)


# --- retry behaviour -------------------------------------------------------


def test_a_rate_limit_is_retried_and_the_result_survives(no_real_waiting):
    session = Session([
        Result("Error: Hevy rate limit exceeded", is_error=True),
        Result('{"items": [{"id": "BENCH"}]}'),
    ])
    assert call(client(session)) == {"items": [{"id": "BENCH"}]}
    assert session.calls == 2


def test_backoff_grows_rather_than_hammering(no_real_waiting):
    session = Session([
        Result("Error: rate limit", is_error=True),
        Result("Error: rate limit", is_error=True),
        Result('{"ok": true}'),
    ])
    call(client(session))
    # Per-minute limits: retrying in milliseconds only spends what is left
    # faster, so the base is deliberately unhurried.
    assert no_real_waiting == [2.0, 4.0]


def test_retries_are_bounded_and_the_original_error_survives(no_real_waiting):
    session = Session([Result("Error: rate limit", is_error=True)] * 3)
    with pytest.raises(MCPRateLimited, match="rate limit"):
        call(client(session))
    assert session.calls == mcp_client.RATE_LIMIT_ATTEMPTS


def test_a_real_fault_is_raised_at_once_without_retrying(no_real_waiting):
    # The whole point of separating the two: a bad key must not take three
    # attempts and six seconds to report itself.
    session = Session([Result("Error: invalid api key", is_error=True)])
    with pytest.raises(MCPError, match="invalid api key"):
        call(client(session))
    assert session.calls == 1
    assert no_real_waiting == []


def test_a_plain_text_failure_is_still_caught(no_real_waiting):
    # Both servers report some failures as ordinary text rather than setting
    # isError. Unchecked, those parse as "no data".
    session = Session([Result("Error: something went wrong")])
    with pytest.raises(MCPError):
        call(client(session))


def test_a_disconnected_client_says_so_rather_than_retrying(no_real_waiting):
    instance = MCPClient("x", [])
    with pytest.raises(MCPError, match="not connected"):
        asyncio.run(instance.call("anything"))
