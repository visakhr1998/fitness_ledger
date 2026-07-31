"""Clients for the two data sources.

Both Hevy and Google Health are reached as stdio MCP servers rather than by
calling their REST APIs directly. That keeps credential handling where it already
works -- the Hevy key stays in the hevy-mcp dotenv, and the health server owns
OAuth refresh -- so this repo never stores a secret.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPError(RuntimeError):
    pass


class MCPTruncatedError(MCPError):
    """The server cut the response short.

    Google Health caps tool output at ~25k characters and appends a notice,
    which leaves the JSON unparseable. Treating that as an empty result would
    silently under-report data, so it is an error the caller must handle by
    asking for a smaller page.
    """


TRUNCATION_MARKER = "--- Response truncated"


class MCPClient:
    """A single long-lived stdio MCP session.

    Used as an async context manager so one subprocess serves a whole sync run
    instead of being restarted per call.
    """

    def __init__(
        self,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
        quiet: bool = True,
    ):
        self.command = command
        self.args = args or []
        # Merge rather than replace: the server still needs PATH and friends.
        self.env = {**os.environ, **(env or {})}
        self.quiet = quiet
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> "MCPClient":
        self._stack = AsyncExitStack()
        params = StdioServerParameters(
            command=self.command, args=self.args, env=self.env
        )
        # Both servers log request traffic to stderr; that is their debug output,
        # not ours, so it goes to the void unless the caller wants it.
        errlog = sys.stderr
        if self.quiet:
            errlog = self._stack.enter_context(open(os.devnull, "w"))
        read, write = await self._stack.enter_async_context(stdio_client(params, errlog=errlog))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None

    async def list_tools(self) -> list[str]:
        if self._session is None:
            raise MCPError("client is not connected")
        result = await self._session.list_tools()
        return [tool.name for tool in result.tools]

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call a tool and return its parsed JSON payload."""
        if self._session is None:
            raise MCPError("client is not connected")
        result = await self._session.call_tool(tool, arguments or {})

        text = "".join(
            block.text for block in result.content if getattr(block, "type", "") == "text"
        )
        if getattr(result, "isError", False):
            raise MCPError(f"{tool} failed: {text[:400]}")
        # Both servers report some failures as ordinary text rather than setting
        # isError. Left unchecked those parse as "no data", which is how a broken
        # request turns into a silently empty week.
        if text.lstrip().startswith("Error:"):
            raise MCPError(f"{tool} failed: {text.strip()[:400]}")
        return _parse(text)


def _parse(text: str) -> Any:
    """Unwrap a tool result.

    Servers vary: some return JSON directly, some return a JSON string nested
    under a single "result" key, some return prose. Handle all three rather than
    letting a format difference look like missing data.
    """
    text = text.strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        if TRUNCATION_MARKER in text:
            raise MCPTruncatedError(
                f"response truncated at {len(text)} chars; retry with a smaller page"
            ) from None
        return {"text": text}
    if (
        isinstance(data, dict)
        and set(data.keys()) == {"result"}
        and isinstance(data["result"], str)
    ):
        try:
            return json.loads(data["result"])
        except json.JSONDecodeError:
            return {"text": data["result"]}
    return data


def hevy_client(config) -> MCPClient:
    return MCPClient(config.hevy_command, config.hevy_args, config.hevy_env)


def health_client(config) -> MCPClient:
    return MCPClient(config.health_command, config.health_args, config.health_env)
