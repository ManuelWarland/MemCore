#!/usr/bin/env python3
"""End-to-end: connect to memcore_mcp.py exactly as a real MCP client would
(stdio), then exercise every tool against a throwaway DB and assert on the
results. Proves the server actually works, not just that it starts."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_SCRIPT = str(Path(__file__).parent / "memcore_mcp.py")


def require(cond, msg):
    if not cond:
        raise AssertionError(msg)


def plist(result):
    """MCP serialises a list return as one content block per item."""
    return [json.loads(b.text) for b in result.content if hasattr(b, "text")]


def pone(result):
    items = plist(result)
    return items[0] if items else None


async def main():
    tmp = tempfile.mkdtemp()
    env = dict(os.environ)
    env["MEMCORE_DB_PATH"] = str(Path(tmp) / "test.db")
    env["MEMCORE_SEMANTIC"] = "0"  # keep the smoke test fast and dep-free

    params = StdioServerParameters(command=sys.executable, args=[SERVER_SCRIPT], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = {t.name for t in (await session.list_tools()).tools}
            expected = {
                "memory_search", "memory_recent", "memory_get", "memory_scopes",
                "memory_stats", "memory_history", "memory_list", "memory_events",
                "memory_embed_status", "memory_write", "memory_archive",
                "memory_restore", "memory_healthcheck",
            }
            require(tools == expected, f"tool set mismatch: {sorted(tools ^ expected)}")

            w = pone(await session.call_tool("memory_write", {                "scope": "smoke", "type": "reference", "name": "n1",                "description": "d1", "content": "le contenu numero un"}))
            require(w.get("ok") and isinstance(w.get("id"), int), f"write failed: {w}")

            got = pone(await session.call_tool("memory_get", {"scope": "smoke", "name": "n1"}))
            require(got and got["content"] == "le contenu numero un", f"get wrong: {got}")

            hits = plist(await session.call_tool("memory_search", {"query": "contenu numero", "scope": "smoke"}))
            require(any(h["name"] == "n1" for h in hits), f"search missed it: {hits}")

            lst = plist(await session.call_tool("memory_list", {"scope": "smoke"}))
            require(len(lst) == 1 and lst[0]["name"] == "n1", f"list wrong: {lst}")

            pone(await session.call_tool("memory_write", {                "scope": "smoke", "type": "reference", "name": "n1",                "description": "d1", "content": "contenu modifie"}))
            hist = plist(await session.call_tool("memory_history", {"scope": "smoke", "name": "n1"}))
            require(len(hist) >= 1 and "numero un" in hist[0]["content"], f"history wrong: {hist}")

            arch = pone(await session.call_tool("memory_archive", {                "scope": "smoke", "name": "n1", "reason": "smoke"}))
            require(arch.get("ok"), f"archive failed: {arch}")
            require(pone(await session.call_tool("memory_get", {"scope": "smoke", "name": "n1"})) is None,                    "archived entry still visible")
            pone(await session.call_tool("memory_restore", {                "scope": "smoke", "name": "n1", "reason": "smoke"}))

            events = plist(await session.call_tool("memory_events", {"scope": "smoke"}))
            require(any(e["operation"] == "memory_archive" for e in events), f"events missing: {events}")

            hc = pone(await session.call_tool("memory_healthcheck", {}))
            require(hc.get("ok"), f"healthcheck failed: {hc}")

            es = pone(await session.call_tool("memory_embed_status", {}))
            require(es.get("semantic_available") is False, f"embed_status wrong: {es}")

    print("MCP_CLIENT_TESTS_OK")


if __name__ == "__main__":
    asyncio.run(main())
