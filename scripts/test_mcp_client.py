#!/usr/bin/env python3
"""End-to-end smoke test: connect to memcore_mcp.py exactly as a real MCP
client would (stdio), list tools, and call memory_search. Proves the server
works before relying on a Claude Code session restart to pick it up."""

import asyncio
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_SCRIPT = str(Path(__file__).parent / "memcore_mcp.py")


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER_SCRIPT])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("Tools exposed:", [t.name for t in tools.tools])

            result = await session.call_tool("memory_search", {"query": "pate a souder"})
            for block in result.content:
                if hasattr(block, "text"):
                    print("\nmemory_search result:\n", block.text[:800])

            stats = await session.call_tool("memory_stats", {})
            for block in stats.content:
                if hasattr(block, "text"):
                    print("\nmemory_stats result:\n", block.text)


if __name__ == "__main__":
    asyncio.run(main())
