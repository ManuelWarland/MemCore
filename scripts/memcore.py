#!/usr/bin/env python3
"""MemCore — central, fast, cross-AI-shareable memory store.

Pure Python stdlib (sqlite3 + FTS5). No native compiled dependencies,
no background daemon, no third-party install-time complexity.

Storage: a single SQLite file. Any tool/AI with file access and a
sqlite3 library can read or write it directly — no proprietary format,
no protocol lock-in. An MCP server (memcore_mcp.py) wraps these same
functions for MCP-native clients.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

for _stream in (sys.stdout, sys.stderr, sys.stdin):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

DB_PATH = Path(os.environ.get("MEMCORE_DB_PATH", Path.home() / "MemCore" / "memcore.db"))
BACKUP_PATH = Path(os.environ.get("MEMCORE_BACKUP_PATH", "E:/vault/_Mémoire Claude Code/_MemCore_Backup/memcore.db"))

VALID_TYPES = {"user", "feedback", "project", "reference"}
MAX_CONTENT_CHARS = 200_000
MAX_FIELD_CHARS = 500


class ValidationError(ValueError):
    pass


class ConflictError(ValidationError):
    pass


SECRET_NAME_RE = re.compile(r"(?:^|[-_.\s])(credentials?|secrets?|passwords?|tokens?|mots?[-_ ]?de[-_ ]?passe)(?:$|[-_.\s])", re.I)
SECRET_CONTENT_PATTERNS = [
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("generic_assignment", re.compile(r"(?im)^\s*(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|mot[_ -]?de[_ -]?passe)\s*[:=]\s*[^\s<>{}\[\]]{12,}\s*$")),
]


def _detect_secret(scope, name, content):
    if SECRET_NAME_RE.search(str(scope)) or SECRET_NAME_RE.search(str(name)):
        return "sensitive_name"
    for code, pattern in SECRET_CONTENT_PATTERNS:
        if pattern.search(str(content)):
            return code
    return None


def _validate_entry(scope, type_, name, content, description):
    if not scope or not scope.strip():
        raise ValidationError("scope must not be empty")
    if not name or not name.strip():
        raise ValidationError("name must not be empty")
    if type_ not in VALID_TYPES:
        raise ValidationError(f"type must be one of {sorted(VALID_TYPES)}, got {type_!r}")
    if len(scope) > MAX_FIELD_CHARS or len(name) > MAX_FIELD_CHARS:
        raise ValidationError(f"scope/name must be under {MAX_FIELD_CHARS} chars")
    if len(description) > MAX_FIELD_CHARS:
        raise ValidationError(f"description must be under {MAX_FIELD_CHARS} chars")
    if not content or not content.strip():
        raise ValidationError("content must not be empty")
    if len(content) > MAX_CONTENT_CHARS:
        raise ValidationError(f"content exceeds {MAX_CONTENT_CHARS} chars ({len(content)})")

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    source_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT,
    archived_by TEXT,
    archive_reason TEXT,
    UNIQUE(scope, name)
);

CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    name, description, content, scope, type,
    content='entries', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
    INSERT INTO entries_fts(rowid, name, description, content, scope, type)
    VALUES (new.id, new.name, new.description, new.content, new.scope, new.type);
END;

CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, name, description, content, scope, type)
    VALUES ('delete', old.id, old.name, old.description, old.content, old.scope, old.type);
END;

CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, name, description, content, scope, type)
    VALUES ('delete', old.id, old.name, old.description, old.content, old.scope, old.type);
    INSERT INTO entries_fts(rowid, name, description, content, scope, type)
    VALUES (new.id, new.name, new.description, new.content, new.scope, new.type);
END;

CREATE INDEX IF NOT EXISTS idx_entries_scope ON entries(scope);
CREATE INDEX IF NOT EXISTS idx_entries_type ON entries(type);

CREATE TABLE IF NOT EXISTS memory_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    operation TEXT NOT NULL,
    actor TEXT NOT NULL,
    origin TEXT NOT NULL,
    session_ref TEXT,
    scope TEXT,
    name TEXT,
    entry_id INTEGER,
    outcome TEXT NOT NULL,
    reason TEXT,
    details_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_memory_events_entry ON memory_events(scope, name, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_events_actor ON memory_events(actor, occurred_at DESC);

-- Audit trail: no write can silently destroy a prior value. Any agent can still
-- upsert (overwrite) an entry -- that is the intended shared-memory model -- but
-- the previous content is always recoverable here, with a timestamp.
CREATE TABLE IF NOT EXISTS entries_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL,
    scope TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    description TEXT NOT NULL,
    content TEXT NOT NULL,
    replaced_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS entries_archive_before_update BEFORE UPDATE ON entries
WHEN old.content IS NOT new.content OR old.description IS NOT new.description OR old.type IS NOT new.type
BEGIN
    INSERT INTO entries_history (entry_id, scope, name, type, description, content, replaced_at)
    VALUES (old.id, old.scope, old.name, old.type, old.description, old.content, new.updated_at);
END;

CREATE TRIGGER IF NOT EXISTS entries_archive_before_delete BEFORE DELETE ON entries
BEGIN
    INSERT INTO entries_history (entry_id, scope, name, type, description, content, replaced_at)
    VALUES (old.id, old.scope, old.name, old.type, old.description, old.content, datetime('now'));
END;
"""


def _migrate(con):
    columns = {row[1] for row in con.execute("PRAGMA table_info(entries)")}
    for column, declaration in (
        ("archived_at", "TEXT"),
        ("archived_by", "TEXT"),
        ("archive_reason", "TEXT"),
    ):
        if column not in columns:
            con.execute(f"ALTER TABLE entries ADD COLUMN {column} {declaration}")

    # Replace the original unconditional FTS triggers. Archived entries must
    # disappear from normal search without losing their durable row.
    con.executescript("""
    DROP TRIGGER IF EXISTS entries_ai;
    DROP TRIGGER IF EXISTS entries_ad;
    DROP TRIGGER IF EXISTS entries_au;
    CREATE TRIGGER entries_ai AFTER INSERT ON entries WHEN new.archived_at IS NULL BEGIN
        INSERT INTO entries_fts(rowid, name, description, content, scope, type)
        VALUES (new.id, new.name, new.description, new.content, new.scope, new.type);
    END;
    CREATE TRIGGER entries_ad AFTER DELETE ON entries WHEN old.archived_at IS NULL BEGIN
        INSERT INTO entries_fts(entries_fts, rowid, name, description, content, scope, type)
        VALUES ('delete', old.id, old.name, old.description, old.content, old.scope, old.type);
    END;
    CREATE TRIGGER entries_au AFTER UPDATE ON entries BEGIN
        INSERT INTO entries_fts(entries_fts, rowid, name, description, content, scope, type)
        SELECT 'delete', old.id, old.name, old.description, old.content, old.scope, old.type
        WHERE old.archived_at IS NULL;
        INSERT INTO entries_fts(rowid, name, description, content, scope, type)
        SELECT new.id, new.name, new.description, new.content, new.scope, new.type
        WHERE new.archived_at IS NULL;
    END;
    """)
    con.commit()


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")  # wait up to 30s on lock contention instead of erroring immediately
    con.executescript(SCHEMA)
    _migrate(con)
    return con


def now():
    return datetime.now(timezone.utc).isoformat()


MAX_QUERY_CHARS = 2000


def _clamp_limit(limit):
    return max(1, min(int(limit), 200))


def _event(con, operation, actor, origin, session_ref=None, scope=None, name=None,
           entry_id=None, outcome="ok", reason=None, details=None):
    con.execute(
        """INSERT INTO memory_events
           (occurred_at, operation, actor, origin, session_ref, scope, name,
            entry_id, outcome, reason, details_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (now(), operation, actor or "unknown", origin or "unknown", session_ref,
         scope, name, entry_id, outcome, reason,
         json.dumps(details, ensure_ascii=False) if details else None),
    )


def add_entry(scope, type_, name, content, description="", source_path=None,
              expected_updated_at=None, actor="legacy", origin="legacy", session_ref=None):
    _validate_entry(scope, type_, name, content, description)
    secret_code = _detect_secret(scope, name, content)
    if secret_code:
        con = connect()
        try:
            _event(con, "memory_write", actor, origin, session_ref, scope, name,
                   outcome="rejected", reason="secret_detected", details={"detector": secret_code})
            con.commit()
        finally:
            con.close()
        raise ValidationError("secret_detected: sensitive content was refused")
    con = connect()
    try:
        ts = now()
        current = con.execute(
            "SELECT id, updated_at, archived_at FROM entries WHERE scope=? AND name=?",
            (scope, name),
        ).fetchone()
        if current:
            if current[2] is not None:
                _event(con, "memory_write", actor, origin, session_ref, scope, name,
                       current[0], "conflict", "entry_archived")
                con.commit()
                raise ConflictError("entry_archived: restore the entry before updating it")
            if expected_updated_at is not None and expected_updated_at != current[1]:
                _event(con, "memory_write", actor, origin, session_ref, scope, name,
                       current[0], "conflict", "stale_version")
                con.commit()
                raise ConflictError("conflict: expected_updated_at does not match current version")
            con.execute(
                """UPDATE entries SET type=?, description=?, content=?, source_path=?, updated_at=?
                   WHERE id=?""",
                (type_, description, content, source_path, ts, current[0]),
            )
            entry_id = current[0]
            operation = "memory_update"
        else:
            cur = con.execute(
                """INSERT INTO entries
                   (scope, type, name, description, content, source_path, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (scope, type_, name, description, content, source_path, ts, ts),
            )
            entry_id = cur.lastrowid
            operation = "memory_create"
        _event(con, operation, actor, origin, session_ref, scope, name, entry_id)
        con.commit()
        return entry_id
    finally:
        con.close()


def _run_fts_query(con, fts_query, scope, limit):
    sql = """
        SELECT e.id, e.scope, e.type, e.name, e.description, e.content,
               e.source_path, e.updated_at,
               snippet(entries_fts, 2, '[', ']', '...', 12) AS snippet,
               bm25(entries_fts) AS rank
        FROM entries_fts
        JOIN entries e ON e.id = entries_fts.rowid
        WHERE entries_fts MATCH ? AND e.archived_at IS NULL
    """
    params = [fts_query]
    if scope:
        sql += " AND e.scope = ?"
        params.append(scope)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    try:
        rows = con.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return None
    return [dict(r) for r in rows]


def search(query, scope=None, limit=20, debug=False):
    """FTS5 search, tolerant of multi-word queries.

    FTS5's MATCH combines space-separated terms with an implicit AND — a
    query like "AgentRoom SQLite multi-agent consultations markdown
    garde-fou" requires ALL SIX terms in the SAME entry, so one term that
    doesn't appear verbatim anywhere (e.g. "garde-fou" when the entry says
    "garde-fous") makes the WHOLE query return 0 results, even though a
    highly relevant entry matches 5 of 6 terms. Found 2026-08-13 via a
    cross-AI handoff from Codex CLI (see vault note
    "0 - INBOX/Handoff Claude Code — améliorer recherche MemCore.md"):
    several real, longer queries against real content returned 0 hits.

    Fix: try the strict AND query first (precise, fast, preferred when it
    works). Only if that returns literally 0 rows AND the query has more
    than one token, retry with the SAME tokens OR'd together — bm25 still
    ranks entries matching more terms higher, so the most relevant partial
    match surfaces first instead of nothing at all.
    """
    query = (query or "").strip()
    if not query:
        return []
    if len(query) > MAX_QUERY_CHARS:
        raise ValidationError(f"query exceeds {MAX_QUERY_CHARS} chars")
    limit = _clamp_limit(limit)
    tokens = query.split()
    and_query = " ".join(f'"{t}"*' for t in tokens)

    con = connect()
    try:
        con.row_factory = sqlite3.Row
        rows = _run_fts_query(con, and_query, scope, limit)
        mode = "and"
        or_query = None
        if not rows and len(tokens) > 1:
            or_query = " OR ".join(f'"{t}"*' for t in tokens)
            or_rows = _run_fts_query(con, or_query, scope, limit)
            if or_rows:
                rows = or_rows
                mode = "or_fallback"
    finally:
        con.close()

    rows = rows or []
    if debug:
        for r in rows:
            r["_search_mode"] = mode
        return {
            "results": rows,
            "mode": mode,
            "and_query": and_query,
            "or_query": or_query,
        }
    return rows


def recent(limit=20, scope=None, include_archived=False):
    limit = _clamp_limit(limit)
    sql = "SELECT id, scope, type, name, description, updated_at FROM entries"
    params = []
    clauses = []
    if not include_archived:
        clauses.append("archived_at IS NULL")
    if scope:
        clauses.append("scope = ?")
        params.append(scope)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    con = connect()
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def delete_entry(scope, name):
    """Physical deletion is reserved for healthcheck probes."""
    if not str(scope).startswith(HEALTHCHECK_SCOPE):
        raise ValidationError("physical_delete_forbidden: use archive_entry")
    con = connect()
    try:
        cur = con.execute("DELETE FROM entries WHERE scope=? AND name=?", (scope, name))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def backup(dest=None):
    dest = Path(dest) if dest else BACKUP_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    src_con = connect()
    dest_con = sqlite3.connect(str(dest))
    try:
        with dest_con:
            src_con.backup(dest_con)  # WAL-safe: consistent snapshot, unlike a raw file copy
    finally:
        dest_con.close()
        src_con.close()
    return str(dest)


def get_entry(scope, name, include_archived=False):
    con = connect()
    try:
        con.row_factory = sqlite3.Row
        sql = "SELECT * FROM entries WHERE scope=? AND name=?"
        if not include_archived:
            sql += " AND archived_at IS NULL"
        row = con.execute(sql, (scope, name)).fetchone()
    finally:
        con.close()
    return dict(row) if row else None


def get_history(scope, name, limit=20):
    con = connect()
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """SELECT scope, name, type, description, content, replaced_at
               FROM entries_history WHERE scope=? AND name=?
               ORDER BY replaced_at DESC LIMIT ?""",
            (scope, name, _clamp_limit(limit)),
        ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def list_scopes(include_archived=False):
    con = connect()
    try:
        rows = con.execute(
            "SELECT scope, COUNT(*) AS n FROM entries " +
            ("" if include_archived else "WHERE archived_at IS NULL ") +
            "GROUP BY scope ORDER BY n DESC"
        ).fetchall()
    finally:
        con.close()
    return [{"scope": r[0], "count": r[1]} for r in rows]


def stats():
    con = connect()
    try:
        total = con.execute("SELECT COUNT(*) FROM entries WHERE archived_at IS NULL").fetchone()[0]
        archived = con.execute("SELECT COUNT(*) FROM entries WHERE archived_at IS NOT NULL").fetchone()[0]
    finally:
        con.close()
    return {"total_entries": total, "archived_entries": archived, "db_path": str(DB_PATH), "scopes": list_scopes()}


def archive_entry(scope, name, reason, actor="legacy", origin="legacy", session_ref=None):
    if not str(reason or "").strip():
        raise ValidationError("archive reason must not be empty")
    con = connect()
    try:
        row = con.execute(
            "SELECT id, archived_at FROM entries WHERE scope=? AND name=?", (scope, name)
        ).fetchone()
        if not row:
            return False
        if row[1] is not None:
            return True
        ts = now()
        con.execute(
            "UPDATE entries SET archived_at=?, archived_by=?, archive_reason=?, updated_at=? WHERE id=?",
            (ts, actor, reason, ts, row[0]),
        )
        _event(con, "memory_archive", actor, origin, session_ref, scope, name, row[0], reason=reason)
        con.commit()
        return True
    finally:
        con.close()


def restore_entry(scope, name, reason, actor="legacy", origin="legacy", session_ref=None):
    if not str(reason or "").strip():
        raise ValidationError("restore reason must not be empty")
    con = connect()
    try:
        row = con.execute(
            "SELECT id, archived_at FROM entries WHERE scope=? AND name=?", (scope, name)
        ).fetchone()
        if not row:
            return False
        if row[1] is None:
            return True
        ts = now()
        con.execute(
            "UPDATE entries SET archived_at=NULL, archived_by=NULL, archive_reason=NULL, updated_at=? WHERE id=?",
            (ts, row[0]),
        )
        _event(con, "memory_restore", actor, origin, session_ref, scope, name, row[0], reason=reason)
        con.commit()
        return True
    finally:
        con.close()


def get_events(scope=None, name=None, limit=50):
    clauses, params = [], []
    if scope is not None:
        clauses.append("scope=?")
        params.append(scope)
    if name is not None:
        clauses.append("name=?")
        params.append(name)
    sql = "SELECT * FROM memory_events"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(_clamp_limit(limit))
    con = connect()
    try:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute(sql, params).fetchall()]
    finally:
        con.close()


HEALTHCHECK_SCOPE = "_healthcheck"
HEALTHCHECK_NAME = "probe"


def healthcheck(scope=None, actor="system", origin="healthcheck", session_ref=None):
    """Quick self-test (~1s), not a theoretical check — exercises the real
    write/read/search/history/delete paths end to end, including the
    AND-then-OR-fallback search behavior (see search()). Uses a dedicated
    throwaway scope so it never touches real data, and always cleans up
    (even on partial failure) via try/except per check rather than one
    try/finally — a single check failing must not skip the ones after it
    or leave the probe entry behind.

    `scope` defaults to HEALTHCHECK_SCOPE ("_healthcheck"). A scope-locked
    MCP connection (memcore_mcp.py --scope X) passes a derived scope
    ("_healthcheck_X") instead — respects the "sandboxed to one scope"
    guarantee (never touches X's real data) without silently escaping the
    lock to write into the shared "_healthcheck" namespace either.

    Added 2026-08-13, suggested by Codex CLI during a cross-AI planning
    discussion (see vault note "3 - UTILITAIRES/Handoff Claude Code —
    améliorer recherche MemCore.md") after the exact failure mode this
    guards against: a connection that LOOKS fine but whose search silently
    misbehaves.
    """
    scope = scope or HEALTHCHECK_SCOPE
    checks = []
    ok = True

    def record(check_name, passed, detail=""):
        nonlocal ok
        checks.append({"check": check_name, "ok": bool(passed), "detail": str(detail)})
        if not passed:
            ok = False

    content = "healthcheck alpha bravo charlie"

    try:
        add_entry(scope, "reference", HEALTHCHECK_NAME, content, description="probe",
                  actor=actor, origin=origin, session_ref=session_ref)
        record("write", True)
    except Exception as e:
        record("write", False, e)
        return {"ok": False, "checks": checks, "db_path": str(DB_PATH)}

    try:
        entry = get_entry(scope, HEALTHCHECK_NAME)
        record("read", entry is not None and entry.get("content") == content,
               "" if entry else "get_entry returned None")
    except Exception as e:
        record("read", False, e)

    try:
        hits = search("healthcheck alpha", scope=scope, limit=5)
        record("search_and", any(h["name"] == HEALTHCHECK_NAME for h in hits), f"{len(hits)} hits")
    except Exception as e:
        record("search_and", False, e)

    try:
        # One real term + one term guaranteed absent from the probe content
        # -> strict AND must return 0, forcing the OR-fallback path.
        dbg = search("healthcheck zzznotarealword", scope=scope, limit=5, debug=True)
        fallback_hit = any(r["name"] == HEALTHCHECK_NAME for r in dbg["results"])
        record("search_or_fallback", dbg["mode"] == "or_fallback" and fallback_hit,
               f"mode={dbg['mode']}, hits={len(dbg['results'])}")
    except Exception as e:
        record("search_or_fallback", False, e)

    try:
        add_entry(scope, "reference", HEALTHCHECK_NAME, content + " delta", description="probe",
                  actor=actor, origin=origin, session_ref=session_ref)
        hist = get_history(scope, HEALTHCHECK_NAME, limit=5)
        record("history", len(hist) >= 1, f"{len(hist)} row(s)")
    except Exception as e:
        record("history", False, e)

    try:
        current = get_entry(scope, HEALTHCHECK_NAME)
        try:
            add_entry(scope, "reference", HEALTHCHECK_NAME, content + " stale",
                      description="probe", expected_updated_at="stale-version",
                      actor=actor, origin=origin, session_ref=session_ref)
            conflict_ok = False
        except ConflictError:
            conflict_ok = True
        unchanged = get_entry(scope, HEALTHCHECK_NAME)
        record("optimistic_conflict", conflict_ok and unchanged["updated_at"] == current["updated_at"])
    except Exception as e:
        record("optimistic_conflict", False, e)

    try:
        archived = archive_entry(scope, HEALTHCHECK_NAME, "healthcheck archive",
                                 actor=actor, origin=origin, session_ref=session_ref)
        hidden = get_entry(scope, HEALTHCHECK_NAME) is None
        durable = get_entry(scope, HEALTHCHECK_NAME, include_archived=True) is not None
        absent_from_search = not any(
            h["name"] == HEALTHCHECK_NAME for h in search("healthcheck alpha", scope=scope, limit=5)
        )
        restored = restore_entry(scope, HEALTHCHECK_NAME, "healthcheck restore",
                                 actor=actor, origin=origin, session_ref=session_ref)
        visible = get_entry(scope, HEALTHCHECK_NAME) is not None
        record("archive_restore", archived and hidden and durable and absent_from_search and restored and visible)
    except Exception as e:
        record("archive_restore", False, e)

    try:
        fake_secret = "sk-proj-" + ("A" * 28)
        try:
            add_entry(scope, "reference", "guard-probe", fake_secret,
                      description="probe", actor=actor, origin=origin, session_ref=session_ref)
            refused = False
        except ValidationError as e:
            refused = str(e).startswith("secret_detected")
        record("secret_guard", refused and get_entry(scope, "guard-probe", include_archived=True) is None)
    except Exception as e:
        record("secret_guard", False, e)

    try:
        deleted = delete_entry(scope, HEALTHCHECK_NAME)
        record("delete", deleted)
    except Exception as e:
        record("delete", False, e)

    return {"ok": ok, "checks": checks, "db_path": str(DB_PATH)}


def main():
    p = argparse.ArgumentParser(description="MemCore CLI")
    p.add_argument("--actor", default="cli", help="Trusted caller identity for audit")
    p.add_argument("--origin", default="terminal", help="Trusted caller origin for audit")
    p.add_argument("--session-ref", default=None, help="Optional session/room reference for audit")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("add")
    sp.add_argument("--scope", required=True)
    sp.add_argument("--type", required=True)
    sp.add_argument("--name", required=True)
    sp.add_argument("--description", default="")
    sp.add_argument("--content", help="Inline content; omit to read from stdin")
    sp.add_argument("--source-path", default=None)
    sp.add_argument("--expected-updated-at", default=None)

    sp = sub.add_parser("search")
    sp.add_argument("query")
    sp.add_argument("--scope", default=None)
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--debug", action="store_true", help="Show which query mode matched (and/or_fallback) plus raw FTS queries")

    sp = sub.add_parser("recent")
    sp.add_argument("--scope", default=None)
    sp.add_argument("--limit", type=int, default=20)

    sp = sub.add_parser("get")
    sp.add_argument("--scope", required=True)
    sp.add_argument("--name", required=True)

    sp = sub.add_parser("delete")
    sp.add_argument("--scope", required=True)
    sp.add_argument("--name", required=True)

    sp = sub.add_parser("archive")
    sp.add_argument("--scope", required=True)
    sp.add_argument("--name", required=True)
    sp.add_argument("--reason", required=True)

    sp = sub.add_parser("restore")
    sp.add_argument("--scope", required=True)
    sp.add_argument("--name", required=True)
    sp.add_argument("--reason", required=True)

    sp = sub.add_parser("events")
    sp.add_argument("--scope", default=None)
    sp.add_argument("--name", default=None)
    sp.add_argument("--limit", type=int, default=50)

    sp = sub.add_parser("backup")
    sp.add_argument("--dest", default=None)

    sp = sub.add_parser("history")
    sp.add_argument("--scope", required=True)
    sp.add_argument("--name", required=True)
    sp.add_argument("--limit", type=int, default=20)

    sub.add_parser("scopes")
    sub.add_parser("stats")
    sub.add_parser("init")
    sub.add_parser("healthcheck")

    args = p.parse_args()

    if args.cmd == "init":
        connect().close()
        print(json.dumps({"ok": True, "db_path": str(DB_PATH)}))
    elif args.cmd == "add":
        content = args.content if args.content is not None else sys.stdin.read()
        try:
            eid = add_entry(
                args.scope, args.type, args.name, content, args.description, args.source_path,
                args.expected_updated_at, args.actor, args.origin, args.session_ref
            )
        except ValidationError as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            return 1
        print(json.dumps({"ok": True, "id": eid}))
    elif args.cmd == "delete":
        try:
            deleted = delete_entry(args.scope, args.name)
            print(json.dumps({"ok": deleted}))
        except ValidationError as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            return 1
    elif args.cmd == "archive":
        try:
            result = archive_entry(args.scope, args.name, args.reason, args.actor, args.origin, args.session_ref)
            print(json.dumps({"ok": result}))
        except ValidationError as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            return 1
    elif args.cmd == "restore":
        try:
            result = restore_entry(args.scope, args.name, args.reason, args.actor, args.origin, args.session_ref)
            print(json.dumps({"ok": result}))
        except ValidationError as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            return 1
    elif args.cmd == "events":
        print(json.dumps(get_events(args.scope, args.name, args.limit), ensure_ascii=False, indent=2))
    elif args.cmd == "backup":
        dest = backup(args.dest)
        print(json.dumps({"ok": True, "dest": dest}))
    elif args.cmd == "history":
        print(json.dumps(get_history(args.scope, args.name, args.limit), ensure_ascii=False, indent=2))
    elif args.cmd == "search":
        try:
            print(json.dumps(search(args.query, args.scope, args.limit, debug=args.debug), ensure_ascii=False, indent=2))
        except ValidationError as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            return 1
    elif args.cmd == "recent":
        print(json.dumps(recent(args.limit, args.scope), ensure_ascii=False, indent=2))
    elif args.cmd == "get":
        r = get_entry(args.scope, args.name)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif args.cmd == "scopes":
        print(json.dumps(list_scopes(), ensure_ascii=False, indent=2))
    elif args.cmd == "stats":
        print(json.dumps(stats(), ensure_ascii=False, indent=2))
    elif args.cmd == "healthcheck":
        result = healthcheck(actor=args.actor, origin=args.origin, session_ref=args.session_ref)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
