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
import sqlite3
import sys
from pathlib import Path
from typing import Annotated

for _stream in (sys.stdout, sys.stderr, sys.stdin):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))
import memcore  # noqa: E402

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402
from pydantic import Field  # noqa: E402

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
    version=memcore.__version__,
)


def _effective_scope(requested):
    """When this connection is scope-locked, no request can ever address a
    different scope — the caller's own `scope` argument is silently ignored
    rather than trusted."""
    return SCOPE_LOCK if SCOPE_LOCK else requested


# ---------------------------------------------------------------------------
# Shared parameter descriptions and tool annotations. They only document the
# tools for the calling AI; behaviour is defined by memcore.py.
# ---------------------------------------------------------------------------

_SCOPE_LOCK_NOTE = ("On a scope-locked connection (--scope), this argument is "
                    "ignored and the connection's own scope is used.")

ScopeFilter = Annotated[str | None, Field(
    description="Restrict to one project/topic scope (list them with memory_scopes). "
                "Omit to cover every scope. " + _SCOPE_LOCK_NOTE)]
ScopeKey = Annotated[str, Field(
    description="Project/topic scope the entry belongs to, e.g. a project folder "
                "name (list them with memory_scopes). " + _SCOPE_LOCK_NOTE)]
EntryName = Annotated[str, Field(
    description="Entry name: the short kebab-case slug that identifies the entry "
                "within its scope.")]
IncludeArchived = Annotated[bool, Field(
    description="Also return archived (soft-deleted) entries. Default false: "
                "archived entries are hidden.")]
AuditReason = Annotated[str, Field(
    description="Why you are doing this, in a few words. Required and must not be "
                "empty; stored in the audit log (memory_events).")]


def _limit(default, what):
    return Annotated[int, Field(
        description=f"Maximum number of {what} to return (default {default}). "
                    "Values are clamped to 1-200.")]


_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                        idempotentHint=True, openWorldHint=False)


def _change(idempotent):
    return ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                           idempotentHint=idempotent, openWorldHint=False)


@server.tool(title="Search memory", annotations=_READ)
def memory_search(
    query: Annotated[str, Field(
        description="Search terms or a natural-language question, in French or English.")],
    scope: ScopeFilter = None,
    limit: _limit(20, "results") = 20,
    debug: Annotated[bool, Field(
        description="If true, return {results, mode, ...} instead of a plain list; `mode` "
                    "tells which path matched (and / or_fallback / hybrid / vector / lexical).")] = False,
    semantic: Annotated[bool | None, Field(
        description="null = hybrid lexical + semantic (default), true = vector search only, "
                    "false = lexical (FTS5) only. Semantic search needs the optional "
                    "embeddings; without them the search is lexical.")] = None,
):
    """Search all remembered facts by content, across every project scope.

    The default entry point. Use it before assuming something is not known:
    it searches EVERY scope at once, so there is no need to guess which
    project a fact was recorded under. Use memory_get instead when you
    already know the exact scope and name, memory_list to browse without a
    query, and memory_recent for what changed lately.

    Two layers, blended by default:
    - Lexical (FTS5): multi-word queries first require ALL terms in one entry,
      then fall back to ANY term (ranked by how many match), so one
      non-verbatim word doesn't zero the result.
    - Semantic (if embeddings are present): vector nearest neighbours on a
      multilingual sentence model, which finds entries about the same idea
      even with no shared keywords.

    Read-only. Archived entries are never returned. Returns a list of
    entries, best match first; an empty list when nothing matches or the
    query is invalid.
    """
    try:
        return memcore.search(query, scope=_effective_scope(scope), limit=limit,
                              debug=debug, semantic=semantic)
    except memcore.ValidationError:
        return [] if not debug else {"results": [], "mode": None, "and_query": None, "or_query": None}


@server.tool(title="Semantic search status", annotations=_READ)
def memory_embed_status() -> dict:
    """Report whether semantic search is active, which embedding model is
    used, and how many entries have a current vector embedding.

    Use it to understand why memory_search finds (or misses) entries that
    share no keywords with the query: entries without a current embedding are
    lexical-only until the next `embed-backfill` run (a CLI command, not an
    MCP tool). For general counts use memory_stats; to test that the store
    works, memory_healthcheck. Read-only, takes no arguments.
    """
    return memcore.embed_status()


@server.tool(title="Recently updated entries", annotations=_READ)
def memory_recent(
    scope: ScopeFilter = None,
    limit: _limit(20, "entries") = 20,
    include_archived: IncludeArchived = False,
) -> list[dict]:
    """List the most recently created or updated memory entries, newest first.

    Use it to catch up on what changed lately (start of a session, after
    another agent worked), optionally within one scope. To find entries by
    content use memory_search; to browse a scope or type without time
    ordering use memory_list; for who changed what, use memory_events.
    Read-only.
    """
    return memcore.recent(limit=limit, scope=_effective_scope(scope), include_archived=include_archived)


@server.tool(title="Get one entry", annotations=_READ)
def memory_get(
    scope: ScopeKey,
    name: EntryName,
    include_archived: IncludeArchived = False,
) -> dict | None:
    """Fetch one memory entry, complete, by its exact scope and name.

    Use it when you already know both keys (from memory_search, memory_list
    or memory_recent results) and need the full, current content before
    relying on it. Returns null when no entry has that scope and name, or
    when it is archived and include_archived is false. Read-only.
    """
    return memcore.get_entry(_effective_scope(scope), name, include_archived=include_archived)


@server.tool(title="Browse entries", annotations=_READ)
def memory_list(
    scope: ScopeFilter = None,
    type: Annotated[str | None, Field(
        description="Only entries of this type: user, feedback, project or reference. "
                    "Omit for every type.")] = None,
    archived: Annotated[bool, Field(
        description="If true, return ONLY archived entries instead of active ones. "
                    "Default false.")] = False,
    limit: _limit(100, "entries") = 100,
) -> list[dict]:
    """Browse entries by scope, type and archived state, WITHOUT a search query.

    Use it to see everything in a project scope (onboarding to a codebase),
    every entry of one type (e.g. all `feedback`), or what has been archived
    (candidates for memory_restore). To find entries by content use
    memory_search; for the latest changes, memory_recent. Read-only. An
    invalid type returns [{"error": ...}].
    """
    try:
        return memcore.list_entries(_effective_scope(scope), type,
                                    True if archived else False, limit)
    except memcore.ValidationError as e:
        return [{"error": str(e)}]


@server.tool(title="Audit log", annotations=_READ)
def memory_events(
    scope: ScopeFilter = None,
    name: Annotated[str | None, Field(
        description="Only events about this entry name. Omit for every entry.")] = None,
    limit: _limit(50, "events") = 50,
) -> list[dict]:
    """Read the append-only audit log, newest first: who wrote, updated,
    archived or restored what, when, from which client, and any conflicts or
    secret redactions.

    Use it to answer "who changed this, and why?". To see the previous
    CONTENT of an entry, use memory_history instead; for the latest entries
    themselves, memory_recent. Read-only and available on any connection;
    scope-locked connections only see their own scope's events.
    """
    return memcore.get_events(_effective_scope(scope), name, limit)


@server.tool(title="List scopes", annotations=_READ)
def memory_scopes() -> list[dict]:
    """List every known project/topic scope with its number of entries.

    Use it first to discover valid `scope` values for the other tools, or to
    see which projects have memory at all. For the total count and the
    database location use memory_stats; to browse the entries of one scope,
    memory_list. Read-only, takes no arguments. A scope-locked connection
    only sees its own scope.
    """
    all_scopes = memcore.list_scopes()
    if SCOPE_LOCK:
        return [s for s in all_scopes if s["scope"] == SCOPE_LOCK]
    return all_scopes


@server.tool(title="Store statistics", annotations=_READ)
def memory_stats() -> dict:
    """Report the total number of entries, the number of archived entries,
    the database file location and the list of scopes.

    Use it for a quick overview or to check which database file this
    connection uses. For per-scope counts use memory_scopes; for semantic
    search coverage, memory_embed_status; to verify the store actually works,
    memory_healthcheck. Read-only, takes no arguments.
    """
    return memcore.stats()


@server.tool(title="Entry history", annotations=_READ)
def memory_history(
    scope: ScopeKey,
    name: EntryName,
    limit: _limit(20, "versions") = 20,
) -> list[dict]:
    """Show the prior versions of one entry that were overwritten or deleted,
    newest first.

    Every write that replaces an entry keeps the previous version
    automatically, so nothing is silently lost even if another agent
    overwrote it. Use it to recover or compare earlier content. For who made
    each change use memory_events; for the current version, memory_get.
    Read-only, available even on a read-only connection. Returns an empty
    list when the entry has no prior versions.
    """
    return memcore.get_history(_effective_scope(scope), name, limit=limit)


if not READONLY:

    @server.tool(title="Write an entry", annotations=_change(idempotent=False))
    def memory_write(
        scope: ScopeKey,
        type: Annotated[str, Field(
            description="Kind of fact: user (who the user is), feedback (guidance on how "
                        "to work, with the reason), project (ongoing work, decisions, "
                        "constraints) or reference (pointers to external resources).")],
        name: Annotated[str, Field(
            description="Short kebab-case slug, unique within the scope. Reusing an "
                        "existing scope + name updates that entry.")],
        content: Annotated[str, Field(
            description="The full memory content, Markdown allowed. Never put secrets "
                        "here: secret-shaped values are redacted.")],
        description: Annotated[str, Field(
            description="One-line summary of what this entry covers, shown in search "
                        "results. Truncated beyond 500 characters.")] = "",
        expected_updated_at: Annotated[str | None, Field(
            description="Optional optimistic lock: the updated_at value you read with "
                        "memory_get. If the entry changed since, nothing is written and "
                        "a conflict error is returned. Omit to write unconditionally.")] = None,
    ) -> dict:
        """Record a new fact, or update an existing one (same scope + name
        updates it in place).

        Search first (memory_search) to avoid creating a duplicate under a
        different name. An update keeps the previous version in
        memory_history and is logged in memory_events, so it can be undone.
        Updating an archived entry is refused: restore it first with
        memory_restore. Secret-shaped values in `content` (API keys, tokens,
        `password: ...` lines) are replaced by `[REDACTED]` before storage; if
        any were, the result includes `"redacted": [<codes>]`.

        Returns {"ok": true, "id": ...} on success, or {"ok": false, "error":
        ...} for an invalid argument, a conflict (stale expected_updated_at or
        archived entry) or a busy database (retry). Not available on
        read-only connections.
        """
        try:
            meta = memcore.add_entry(
                _effective_scope(scope), type, name, content, description,
                expected_updated_at=expected_updated_at,
                actor=ACTOR, origin=ORIGIN, session_ref=SESSION_REF,
                return_meta=True,
            )
        except memcore.ValidationError as e:
            return {"ok": False, "error": str(e)}
        except sqlite3.OperationalError as e:
            # e.g. "database is locked" — another writer (a `sync` run) held the
            # lock past busy_timeout. Retryable; don't crash the tool call.
            return {"ok": False, "error": f"db_busy: {e} — retry"}
        out = {"ok": True, "id": meta["id"]}
        if meta["redacted"]:
            out["redacted"] = meta["redacted"]  # secret-shaped values were stripped before storing
        return out

    @server.tool(title="Archive an entry", annotations=_change(idempotent=True))
    def memory_archive(scope: ScopeKey, name: EntryName, reason: AuditReason) -> dict:
        """Archive (soft-delete) one entry: hide it from normal reads and
        search while keeping it, restorable with memory_restore and audited
        in memory_events.

        Use it for a fact that became wrong or obsolete; to correct a fact,
        prefer memory_write on the same scope + name. Nothing is ever
        physically deleted. Returns {"ok": true} if the entry is now archived
        (also when it already was), {"ok": false} if no entry has that scope
        and name, or {"ok": false, "error": ...} when the reason is empty.
        Not available on read-only connections.
        """
        try:
            archived = memcore.archive_entry(
                _effective_scope(scope), name, reason, ACTOR, ORIGIN, SESSION_REF
            )
        except memcore.ValidationError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": archived}

    @server.tool(title="Restore an entry", annotations=_change(idempotent=True))
    def memory_restore(scope: ScopeKey, name: EntryName, reason: AuditReason) -> dict:
        """Restore one archived entry so it appears again in normal reads and
        search. The inverse of memory_archive.

        Find archived entries with memory_list(archived=true). The restore is
        logged in memory_events with your reason. Returns {"ok": true} if the
        entry is now active (also when it was not archived), {"ok": false} if
        no entry has that scope and name, or {"ok": false, "error": ...} when
        the reason is empty. Not available on read-only connections.
        """
        try:
            restored = memcore.restore_entry(
                _effective_scope(scope), name, reason, ACTOR, ORIGIN, SESSION_REF
            )
        except memcore.ValidationError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": restored}

    @server.tool(title="Self-test", annotations=_change(idempotent=True))
    def memory_healthcheck() -> dict:
        """Self-test MemCore end to end (about 1 s): write, read, search
        (strict AND and OR-fallback modes), history and delete.

        Use it instead of assuming a connection is healthy just because it is
        listed: a connection can look fine while search silently misbehaves.
        The test only writes to a throwaway scope (`_healthcheck`, or
        `_healthcheck_<scope>` on a scope-locked connection) and always cleans
        up; real entries are never touched. Returns {ok, checks[], db_path}.
        Not available on read-only connections; use memory_stats there.
        """
        probe_scope = f"{memcore.HEALTHCHECK_SCOPE}_{SCOPE_LOCK}" if SCOPE_LOCK else None
        return memcore.healthcheck(
            scope=probe_scope, actor=ACTOR, origin=ORIGIN, session_ref=SESSION_REF
        )


if __name__ == "__main__":
    server.run(transport="stdio")
