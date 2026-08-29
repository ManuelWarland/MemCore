#!/usr/bin/env python3
"""Verify the 4 access profiles actually enforce what they claim, against a
throwaway DB seeded with two scopes. A sandboxed connection must not be
talk-out-able of its boundary, and a read-only one must not expose write tools."""

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
WRITE_TOOLS = {"memory_write", "memory_archive", "memory_restore", "memory_healthcheck"}
SCOPE_A = "proj-alpha"
SCOPE_B = "proj-beta"


def require(cond, msg):
    if not cond:
        raise AssertionError(msg)


def plist(result):
    """MCP serialises a list return as one content block per item."""
    return [json.loads(b.text) for b in result.content if hasattr(b, "text")]


def pone(result):
    items = plist(result)
    return items[0] if items else None


async def connect(env, extra_args):
    params = StdioServerParameters(command=sys.executable, args=[SERVER_SCRIPT] + extra_args, env=env)
    return stdio_client(params)


async def main():
    tmp = tempfile.mkdtemp()
    env = dict(os.environ)
    env["MEMCORE_DB_PATH"] = str(Path(tmp) / "test.db")
    env["MEMCORE_SEMANTIC"] = "0"

    # Seed two scopes via a full-access connection.
    async with await connect(env, []) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for sc in (SCOPE_A, SCOPE_B):
                pone(await s.call_tool("memory_write", {                    "scope": sc, "type": "reference", "name": "seed",                    "description": "d", "content": f"secret de {sc}"}))

    # Full access: all tools, both scopes visible.
    async with await connect(env, []) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = {t.name for t in (await s.list_tools()).tools}
            require(WRITE_TOOLS <= tools, "full access missing write tools")
            scopes = {x["scope"] for x in plist(await s.call_tool("memory_scopes", {}))}
            require({SCOPE_A, SCOPE_B} <= scopes, f"full access can't see both scopes: {scopes}")

    # Read-only: no write tools, still sees everything.
    async with await connect(env, ["--readonly"]) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = {t.name for t in (await s.list_tools()).tools}
            require(not (WRITE_TOOLS & tools), f"read-only exposes write tools: {WRITE_TOOLS & tools}")
            hits = plist(await s.call_tool("memory_search", {"query": "secret"}))
            require(len(hits) == 2, f"read-only should see both entries: {[h['scope'] for h in hits]}")

    # Sandboxed to SCOPE_A: asking for SCOPE_B must yield nothing from B.
    async with await connect(env, ["--scope", SCOPE_A]) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            hits = plist(await s.call_tool("memory_search", {"query": "secret", "scope": SCOPE_B}))
            seen = {h["scope"] for h in hits}
            require(seen <= {SCOPE_A}, f"sandbox breached — saw {seen} when asking for {SCOPE_B}")
            g = pone(await s.call_tool("memory_get", {"scope": SCOPE_B, "name": "seed"}))
            require(g is None or g["scope"] == SCOPE_A, f"sandbox get breached: {g}")
            scopes = {x["scope"] for x in plist(await s.call_tool("memory_scopes", {}))}
            require(scopes <= {SCOPE_A}, f"sandbox scopes list leaked: {scopes}")
            # write is allowed but only into the locked scope
            wr = pone(await s.call_tool("memory_write", {                "scope": SCOPE_B, "type": "reference", "name": "x", "description": "d", "content": "c"}))
            require(wr.get("ok"), f"sandboxed write failed: {wr}")
            g2 = pone(await s.call_tool("memory_get", {"scope": SCOPE_A, "name": "x"}))
            require(g2 is not None, "sandboxed write did not land in the locked scope")

    # Read-only + sandboxed: no writes, one scope only.
    async with await connect(env, ["--readonly", "--scope", SCOPE_A]) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = {t.name for t in (await s.list_tools()).tools}
            require(not (WRITE_TOOLS & tools), "ro+sandbox exposes write tools")
            hits = plist(await s.call_tool("memory_search", {"query": "secret"}))
            require({h["scope"] for h in hits} <= {SCOPE_A}, f"ro+sandbox leaked scopes: {hits}")

    print("ACCESS_PROFILES_TESTS_OK")


if __name__ == "__main__":
    asyncio.run(main())
