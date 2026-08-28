#!/usr/bin/env python3
"""Verify roster MCP identities and Phase A tools through real stdio clients."""

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import memcore

SERVER_SCRIPT = str(Path(__file__).parent / "memcore_mcp.py")
EXPECTED_TOOLS = {
    "memory_search", "memory_recent", "memory_get", "memory_scopes",
    "memory_stats", "memory_history", "memory_write", "memory_archive",
    "memory_restore", "memory_healthcheck",
}


async def check(actor):
    params = StdioServerParameters(
        command=sys.executable,
        args=[SERVER_SCRIPT, "--actor", actor, "--origin", "terminal", "--session-ref", f"phase-b:{actor}"],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            if tools != EXPECTED_TOOLS:
                raise AssertionError(f"{actor}: unexpected tools {sorted(tools)}")
            result = await session.call_tool("memory_healthcheck", {})
            payload = json.loads(result.content[0].text)
            if not payload.get("ok"):
                raise AssertionError(f"{actor}: healthcheck failed {payload}")
    events = memcore.get_events("_healthcheck", None, 200)
    if not any(event["actor"] == actor and event["session_ref"] == f"phase-b:{actor}" for event in events):
        raise AssertionError(f"{actor}: provenance event missing")
    print(f"{actor}: OK")


async def main():
    for actor in ("claude", "codex", "kimi"):
        await check(actor)
    print("PHASE_B_CLIENTS_OK")


if __name__ == "__main__":
    asyncio.run(main())
