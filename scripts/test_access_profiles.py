#!/usr/bin/env python3
"""Verify the 4 access profiles actually enforce what they claim."""

import asyncio
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_SCRIPT = str(Path(__file__).parent / "memcore_mcp.py")


async def check(label, extra_args):
    params = StdioServerParameters(command=sys.executable, args=[SERVER_SCRIPT] + extra_args)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            has_write = "memory_write" in tools

            scopes_result = await session.call_tool("memory_scopes", {})
            scopes_text = scopes_result.content[0].text if scopes_result.content else "[]"

            # Deliberately ask for a DIFFERENT scope than any lock, to prove a
            # sandboxed connection cannot be talked out of its boundary.
            bypass_attempt = await session.call_tool(
                "memory_search", {"query": "MailGuard", "scope": "d--ArduinoProject-MailGuardian"}
            )
            bypass_text = bypass_attempt.content[0].text if bypass_attempt.content else "[]"
            scopes_seen = set()
            import re as _re
            for m in _re.finditer(r'"scope":\s*"([^"]+)"', bypass_text):
                scopes_seen.add(m.group(1))

            print(f"=== {label} ===")
            print("  tools:", sorted(tools))
            print("  has write tools:", has_write)
            print("  scopes visible via memory_scopes:", scopes_text[:200].replace("\n", " "))
            print("  requested scope=d--ArduinoProject-MailGuardian, actually got scopes:", scopes_seen or "(no results)")
            print()


async def main():
    await check("Full access (Claude Code today)", [])
    await check("Read-only, all scopes", ["--readonly"])
    await check("Sandboxed to Marlin-CR10", ["--scope", "d--ArduinoProject-Marlin-CR10"])
    await check("Read-only + sandboxed to Marlin-CR10", ["--readonly", "--scope", "d--ArduinoProject-Marlin-CR10"])


if __name__ == "__main__":
    asyncio.run(main())
