#!/usr/bin/env python3
"""MemCore MCP server — stdio transport.

Registered directly as a local command (not via the Claude Code plugin
marketplace), so it is immune to the Windows marketplace-wipe bug that
broke claude-mem. Any MCP-capable client (Claude Code, Claude Desktop,
others) can add this same command to talk to the same memcore.db.

Access control is decided per CONNECTION, not baked into the database:
    python memcore_mcp.py                        full read/write, all scopes
    python memcore_mcp.py --readonly              read-only, all scopes
    python memcore_mcp.py --scope collab          read/write sandboxed to one scope
    python memcore_mcp.py --readonly --scope collab   read-only view of one scope
Register a separate mcpServers entry per client with the flags matching how
much you trust that particular AI. Least-privilege by default for anything
you haven't decided to fully trust yet.
"""

import argparse
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr, sys.stdin):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))
import memcore  # noqa: E402

from mcp.server.mcpserver import MCPServer  # noqa: E402

_parser = argparse.ArgumentParser()
_parser.add_argument("--readonly", action="store_true", help="Do not expose write/delete tools")
_parser.add_argument("--scope", default=None, help="Sandbox this connection to a single scope")
_parser.add_argument("--actor", default="legacy-mcp", help="Trusted AI/client identity for audit")
_parser.add_argument("--origin", default="terminal", help="Trusted connection origin for audit")
_parser.add_argument("--session-ref", default=None, help="Optional session reference for audit")
_args, _ = _parser.parse_known_args()

READONLY = _args.readonly
SCOPE_LOCK = _args.scope
ACTOR = _args.actor
ORIGIN = _args.origin
SESSION_REF = _args.session_ref

_title = "MemCore"
if SCOPE_LOCK:
    _title += f" (scope: {SCOPE_LOCK})"
if READONLY:
    _title += " [read-only]"

server = MCPServer(
    name="memcore",
    title=_title,
    description="Central, fast, cross-project memory store shared across AI tools.",
)


def _effective_scope(requested):
    """When this connection is scope-locked, no request can ever address a
    different scope — the caller's own `scope` argument is silently ignored
    rather than trusted."""
    return SCOPE_LOCK if SCOPE_LOCK else requested


@server.tool()
def memory_search(query: str, scope: str | None = None, limit: int = 20, debug: bool = False):
    """Full-text search across all remembered facts, across every project scope.

    Use this before assuming something isn't known — it searches EVERY project
    scope at once (no need to guess which project a fact was recorded under).

    A multi-word query first tries to match ALL terms in the same entry
    (precise). If that finds nothing, it automatically retries matching ANY
    of the terms, ranked so entries hitting more terms surface first — so a
    long natural-language query won't silently return empty just because
    one word (an accent variant, a compound, a technical token) isn't a
    verbatim match anywhere. If this STILL returns nothing, retry once with
    2-3 simple keywords before concluding the info isn't recorded.

    Args:
        query: Search terms (French or English, partial words work).
        scope: Optional — restrict to one project scope (e.g. a project folder name).
        limit: Max results to return.
        debug: If true, returns {results, mode, and_query, or_query} instead
            of a bare list — use to see whether the strict or fallback mode
            matched, and the raw FTS queries that were run.
    """
    try:
        return memcore.search(query, scope=_effective_scope(scope), limit=limit, debug=debug)
    except memcore.ValidationError:
        return [] if not debug else {"results": [], "mode": None, "and_query": None, "or_query": None}


@server.tool()
def memory_recent(scope: str | None = None, limit: int = 20, include_archived: bool = False) -> list[dict]:
    """List the most recently updated memory entries, newest first."""
    return memcore.recent(limit=limit, scope=_effective_scope(scope), include_archived=include_archived)


@server.tool()
def memory_get(scope: str, name: str, include_archived: bool = False) -> dict | None:
    """Fetch one exact memory entry by its scope and name."""
    return memcore.get_entry(_effective_scope(scope), name, include_archived=include_archived)


@server.tool()
def memory_scopes() -> list[dict]:
    """List every known project/topic scope and how many entries each has."""
    all_scopes = memcore.list_scopes()
    if SCOPE_LOCK:
        return [s for s in all_scopes if s["scope"] == SCOPE_LOCK]
    return all_scopes


@server.tool()
def memory_stats() -> dict:
    """Report total entry count and database location."""
    return memcore.stats()


@server.tool()
def memory_history(scope: str, name: str, limit: int = 20) -> list[dict]:
    """Show prior versions of an entry that were overwritten or deleted.

    Every write/delete is archived automatically before it happens — nothing
    is ever silently lost, even if another agent overwrites this entry. This
    is a read operation, available even on a read-only connection.
    """
    return memcore.get_history(_effective_scope(scope), name, limit=limit)


if not READONLY:

    @server.tool()
    def memory_write(
        scope: str,
        type: str,
        name: str,
        content: str,
        description: str = "",
        expected_updated_at: str | None = None,
    ) -> dict:
        """Record a new fact, or update an existing one (same scope+name upserts).

        Args:
            scope: Project/topic scope this fact belongs to.
            type: One of user / feedback / project / reference.
            name: Short kebab-case slug, unique within the scope.
            content: The full memory content.
            description: One-line summary of what this entry covers.
        """
        try:
            entry_id = memcore.add_entry(
                _effective_scope(scope), type, name, content, description,
                expected_updated_at=expected_updated_at,
                actor=ACTOR, origin=ORIGIN, session_ref=SESSION_REF,
            )
        except memcore.ValidationError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "id": entry_id}

    @server.tool()
    def memory_archive(scope: str, name: str, reason: str) -> dict:
        """Soft-delete an entry: hide it from normal reads while keeping it restorable and audited."""
        try:
            archived = memcore.archive_entry(
                _effective_scope(scope), name, reason, ACTOR, ORIGIN, SESSION_REF
            )
        except memcore.ValidationError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": archived}

    @server.tool()
    def memory_restore(scope: str, name: str, reason: str) -> dict:
        """Restore one archived entry to normal reads and search."""
        try:
            restored = memcore.restore_entry(
                _effective_scope(scope), name, reason, ACTOR, ORIGIN, SESSION_REF
            )
        except memcore.ValidationError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": restored}

    @server.tool()
    def memory_healthcheck() -> dict:
        """Self-test MemCore end to end (~1s): write, read, search (strict
        AND and OR-fallback modes), history, delete. Use this instead of
        assuming a connection is healthy just because it's listed — a
        connection can look fine while search silently misbehaves (see
        the 2026-08-13 FTS5 AND-vs-OR fix). Returns {ok, checks[], db_path}.
        """
        probe_scope = f"{memcore.HEALTHCHECK_SCOPE}_{SCOPE_LOCK}" if SCOPE_LOCK else None
        return memcore.healthcheck(
            scope=probe_scope, actor=ACTOR, origin=ORIGIN, session_ref=SESSION_REF
        )


if __name__ == "__main__":
    server.run(transport="stdio")
