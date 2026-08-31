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

__version__ = "0.2.0"
# Only used by the opt-in `check-updates` subcommand — the single command that
# ever makes a network call. Nothing else in MemCore talks to the outside.
REPO = "ManuelWarland/MemCore"

DB_PATH = Path(os.environ.get("MEMCORE_DB_PATH", Path.home() / "MemCore" / "memcore.db"))
# Default backup destination for `memcore.py backup`. Override with the
# MEMCORE_BACKUP_PATH env var or the --dest flag (e.g. to drop the copy inside
# a synced/backed-up folder).
BACKUP_PATH = Path(os.environ.get("MEMCORE_BACKUP_PATH", Path.home() / "MemCore" / "backups" / "memcore.db"))

VALID_TYPES = {"user", "feedback", "project", "reference"}
MAX_CONTENT_CHARS = 200_000
MAX_FIELD_CHARS = 500

# ─── Optional semantic search ────────────────────────────────────────────────
# Lexical FTS5 is always on. If `fastembed` + `sqlite-vec` are installed, entries
# also get a vector embedding (computed lazily by `embed-backfill`, never on the
# write path) and `search` blends FTS with vector nearest-neighbours. Disable
# explicitly with MEMCORE_SEMANTIC=0.
SEMANTIC_MODEL = os.environ.get(
    "MEMCORE_EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-mpnet-base-v2")
EMBED_DIM = int(os.environ.get("MEMCORE_EMBED_DIM", "768"))
_SEMANTIC_OFF = os.environ.get("MEMCORE_SEMANTIC", "1") == "0"
_embedder = None
_semantic_import_ok = None


def semantic_available():
    """True if the optional deps import. Cached. Does NOT load the model."""
    global _semantic_import_ok
    if _semantic_import_ok is None:
        if _SEMANTIC_OFF:
            _semantic_import_ok = False
        else:
            try:
                import fastembed  # noqa: F401
                import sqlite_vec  # noqa: F401
                _semantic_import_ok = True
            except Exception:
                _semantic_import_ok = False
    return _semantic_import_ok


_embedder_lock = None


def _get_embedder():
    global _embedder, _embedder_lock
    if _embedder is not None:
        return _embedder
    if _embedder_lock is None:
        import threading
        _embedder_lock = threading.Lock()
    with _embedder_lock:
        if _embedder is None:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # fastembed's mean-pooling notice; model works fine
                from fastembed import TextEmbedding
                _embedder = TextEmbedding(SEMANTIC_MODEL)
    return _embedder


_embedder_loading = False


def _embedder_ready(wait=False):
    """Is the model in memory? If not and `wait` is False, kick off a one-time
    background load and return False (the caller uses lexical this round, the
    next call gets vectors) — so a long-lived server never stalls 5-15s on the
    first search. If `wait` is True (a short-lived CLI that asked for semantic
    explicitly), block and load it now."""
    global _embedder_loading
    if _embedder is not None:
        return True
    if wait:
        _get_embedder()
        return True
    if not _embedder_loading:
        _embedder_loading = True
        import threading
        threading.Thread(target=_get_embedder, daemon=True).start()
    return False


def _embed_texts(texts):
    """Embed and L2-normalise each vector, so sqlite-vec's default L2 KNN
    ranks by cosine similarity."""
    out = []
    for v in _get_embedder().embed(list(texts), batch_size=16):
        v = [float(x) for x in v]
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        out.append([x / norm for x in v])
    return out


def _embed_input(name, description, content):
    # The slug + the curated one-line description carry the strongest signal for
    # "what is this about"; a short content excerpt adds detail without letting a
    # long multi-topic note turn into a fuzzy blob.
    slug = name.replace("-", " ").replace("_", " ")
    return f"{slug}\n{description}\n{(content or '')[:1000]}"


def _load_vec(con):
    """Load the sqlite-vec extension into `con`. Returns True on success."""
    if not semantic_available():
        return False
    try:
        import sqlite_vec
        con.enable_load_extension(True)
        try:
            sqlite_vec.load(con)
        finally:
            con.enable_load_extension(False)
        return True
    except Exception:
        return False


class ValidationError(ValueError):
    pass


class ConflictError(ValidationError):
    pass


# Secrets are REDACTED, not rejected (changed 2026-08-29). Rejecting the whole
# entry silently lost legitimate notes that merely *discuss* a secret format,
# and a slug containing "token"/"secret" (e.g. a note literally about token
# costs) tripped a name-based check. Now: strip the value, keep the note, flag
# it. `credentials_*.md` files stay excluded from import by filename (see the
# importers) — that convention is unchanged.
_SECRET_LABEL = (r"api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret"
                 r"|secret[_ -]?key|auth[_ -]?token|bearer|password|passwd|pwd"
                 r"|mot[_ -]?de[_ -]?passe|psk")

SECRET_CONTENT_PATTERNS = [
    # Private key: from BEGIN to the -----END----- marker, or to a blank line,
    # or to end of text if truncated — so a pasted key body is stripped even
    # without its footer, without eating unrelated paragraphs after it.
    ("private_key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\n[^\S\r\n]*\n|\Z)", re.DOTALL)),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("openai_anthropic_key", re.compile(r"\bsk-(?:ant-|proj-|svcacct-)?[A-Za-z0-9_-]{32,}\b")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("telegram_token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("conn_string", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]{4,}@")),  # scheme://user:pass@host
    ("auth_header", re.compile(r"(?i)(?:authorization|proxy-authorization)[^\S\r\n]*:[^\S\r\n]*(?:bearer|basic|token)[^\S\r\n]+[A-Za-z0-9._~+/=-]{12,}")),
    # A whole line whose sole payload after "<label>:" is one 8+ char token
    # with no path/URL separator — digit NOT required (diceware/word passwords,
    # wifi keys). The no-slash rule keeps "api_key = docs/where/it.md" pointers.
    ("labelled_secret_line", re.compile(
        r"(?im)^([^\S\r\n]*(?:" + _SECRET_LABEL + r")[^\S\r\n]*[:=][^\S\r\n]*['\"`]?)"
        r"[^\s/\\]{8,}(['\"`]?)[^\S\r\n]*$")),
    # Same label mid-line: stricter (12+ chars, contains a digit, no path/URL
    # chars) — cuts "password: chapter-3-of-the-manual" style false positives.
    ("labelled_secret_inline", re.compile(
        r"(?i)((?:" + _SECRET_LABEL + r")[^\S\r\n]*[:=][^\S\r\n]*['\"`]?)"
        r"(?=[^\s<>{}\[\]'\"`/]*\d)[^\s<>{}\[\]'\"`/]{12,}(['\"`]?)")),
]


_LABELLED_CODES = {"labelled_secret_line", "labelled_secret_inline"}


def redact_secrets(text):
    """Replace secret-shaped substrings with a marker. Returns (text, codes)."""
    text = str(text)
    codes = []
    for code, pattern in SECRET_CONTENT_PATTERNS:
        repl = r"\1[REDACTED]\2" if code in _LABELLED_CODES else "[REDACTED]"
        text, n = pattern.subn(repl, text)
        if n:
            codes.append(code)
    return text, codes


DESC_MAX = MAX_FIELD_CHARS


def _clip_description(description):
    description = str(description or "")
    if len(description) <= DESC_MAX:
        return description
    return description[:DESC_MAX - 1].rstrip() + "…"


def _validate_entry(scope, type_, name, content, description):
    if not scope or not scope.strip():
        raise ValidationError("scope must not be empty")
    if not name or not name.strip():
        raise ValidationError("name must not be empty")
    if type_ not in VALID_TYPES:
        raise ValidationError(f"type must be one of {sorted(VALID_TYPES)}, got {type_!r}")
    if len(scope) > MAX_FIELD_CHARS or len(name) > MAX_FIELD_CHARS:
        raise ValidationError(f"scope/name must be under {MAX_FIELD_CHARS} chars")
    if len(description) > MAX_FIELD_CHARS:  # caller should have clipped; hard guard
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


SCHEMA_VERSION = 3  # bump + add a step in _migrate() when the schema changes


def _migrate(con):
    """Bring the DB schema up to SCHEMA_VERSION. Idempotent; gated on
    PRAGMA user_version so it does no work on an already-current DB."""
    if con.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
        return

    columns = {row[1] for row in con.execute("PRAGMA table_info(entries)")}
    for column, declaration in (
        ("archived_at", "TEXT"),
        ("archived_by", "TEXT"),
        ("archive_reason", "TEXT"),
        ("source_mtime", "REAL"),  # v2: last-imported mtime of source_path, for incremental `sync`
        ("embedded_at", "TEXT"),   # v3: when the vector embedding was last computed (NULL = stale/missing)
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
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    con.commit()


def connect(with_vec=None):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")  # wait up to 30s on lock contention instead of erroring immediately
    con.executescript(SCHEMA)
    _migrate(con)
    # sqlite-vec: load the extension and ensure the vector table exists, only
    # when semantic search is available (or explicitly requested).
    # Only load the vector extension when a caller explicitly asks for it
    # (search with semantic on, embed_backfill). Everything else — recent, get,
    # stats, add, sync, the JSONL bridge — must NOT pay `import fastembed`.
    if with_vec:
        _ensure_vec_table(con)
    return con


def _ensure_vec_table(con):
    """Load sqlite-vec and make sure `entries_vec` exists at the right dimension.
    If a table from a different embedding model is found, drop it and mark every
    entry for re-embedding. Guarded so a concurrent opener can't crash on it."""
    if not _load_vec(con):
        return False
    try:
        existing = con.execute(
            "SELECT sql FROM sqlite_master WHERE name='entries_vec'").fetchone()
        if existing and f"FLOAT[{EMBED_DIM}]" not in existing[0]:
            con.execute("DROP TABLE IF EXISTS entries_vec")
            con.execute("UPDATE entries SET embedded_at=NULL")
            con.commit()
            existing = None
        if not existing:
            con.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS entries_vec USING vec0("
                f"entry_id INTEGER PRIMARY KEY, embedding FLOAT[{EMBED_DIM}])")
        return True
    except sqlite3.OperationalError:
        return con.execute("SELECT 1 FROM sqlite_master WHERE name='entries_vec'").fetchone() is not None


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
              expected_updated_at=None, actor="legacy", origin="legacy", session_ref=None,
              source_mtime=None, return_meta=False, _con=None):
    description = _clip_description(description)
    _validate_entry(scope, type_, name, content, description)
    content, redactions = redact_secrets(content)

    con = _con or connect()
    owns_con = _con is None
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
                if owns_con:
                    con.commit()
                raise ConflictError("entry_archived: restore the entry before updating it")
            if expected_updated_at is not None and expected_updated_at != current[1]:
                _event(con, "memory_write", actor, origin, session_ref, scope, name,
                       current[0], "conflict", "stale_version")
                if owns_con:
                    con.commit()
                raise ConflictError("conflict: expected_updated_at does not match current version")
            con.execute(
                """UPDATE entries SET type=?, description=?, content=?, source_path=?,
                   source_mtime=?, updated_at=?, embedded_at=NULL WHERE id=?""",
                (type_, description, content, source_path, source_mtime, ts, current[0]),
            )
            entry_id = current[0]
            operation = "memory_update"
        else:
            cur = con.execute(
                """INSERT INTO entries
                   (scope, type, name, description, content, source_path, source_mtime, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (scope, type_, name, description, content, source_path, source_mtime, ts, ts),
            )
            entry_id = cur.lastrowid
            operation = "memory_create"
        _event(con, operation, actor, origin, session_ref, scope, name, entry_id,
               outcome="redacted" if redactions else "ok",
               reason="secret_redacted" if redactions else None,
               details={"redacted": redactions} if redactions else None)
        if owns_con:
            con.commit()
        if return_meta:
            return {"id": entry_id, "redacted": redactions}
        return entry_id
    finally:
        if owns_con:
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


def _vector_search(con, query, scope, limit, wait_for_model=False):
    """K-nearest non-archived, still-fresh entries by embedding distance.
    Returns list of dicts with `vdist`, or None if the vector layer isn't
    ready (model still loading, no embeddings, extension absent)."""
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='entries_vec'").fetchone() is None:
        return None
    total_vecs = con.execute("SELECT COUNT(*) FROM entries_vec").fetchone()[0]
    if total_vecs == 0:
        return None
    if not _embedder_ready(wait=wait_for_model):
        return None
    try:
        qvec = _vec_to_blob(_embed_texts([query])[0])
    except Exception:
        return None
    # sqlite-vec 0.1.x can't filter metadata inside the KNN. When a scope is
    # given, ask for every vector and filter after (cheap at this scale) so a
    # niche scope isn't crowded out by a dominant one.
    k = total_vecs if scope else max(limit * 4, 20)
    sql = """
        SELECT e.id, e.scope, e.type, e.name, e.description, e.content,
               e.source_path, e.updated_at, v.distance AS vdist
        FROM entries_vec v
        JOIN entries e ON e.id = v.entry_id
        WHERE v.embedding MATCH ? AND k = ?
          AND e.archived_at IS NULL AND e.embedded_at IS NOT NULL
    """
    rows = [dict(r) for r in con.execute(sql, [qvec, k]).fetchall()]
    for r in rows:
        r.setdefault("snippet", None)  # shape parity with FTS rows
    if scope:
        # SECURITY: a scope-locked connection must never see another scope's rows.
        rows = [r for r in rows if r["scope"] == scope]
    return rows[:limit * 4]


def search(query, scope=None, limit=20, debug=False, semantic=None, wait_for_model=False):
    """Search remembered facts.

    Lexical layer (always): FTS5 MATCH combines terms with implicit AND. If the
    strict AND query returns 0 rows and there's more than one token, it retries
    with the terms OR'd (bm25 still ranks entries matching more terms first) —
    so one non-verbatim word (accent variant, compound) doesn't zero the query.

    Semantic layer (if `fastembed` + `sqlite-vec` are installed and embeddings
    exist): vector nearest-neighbours on a multilingual sentence embedding.

    `semantic`: None = hybrid (blend FTS + vector via reciprocal-rank fusion,
    the best default), True = vector only, False = lexical only.
    """
    query = (query or "").strip()
    if not query:
        return []
    if len(query) > MAX_QUERY_CHARS:
        raise ValidationError(f"query exceeds {MAX_QUERY_CHARS} chars")
    limit = _clamp_limit(limit)
    tokens = query.split()
    and_query = " ".join(f'"{t}"*' for t in tokens)
    use_semantic = semantic is not False and semantic_available()

    con = connect(with_vec=use_semantic)
    try:
        con.row_factory = sqlite3.Row
        mode, or_query = "and", None
        # Always run FTS (microseconds) so `semantic=True` still has something
        # real to fall back on before any embeddings exist.
        fts_rows = _run_fts_query(con, and_query, scope, limit) or []
        if not fts_rows and len(tokens) > 1:
            or_query = " OR ".join(f'"{t}"*' for t in tokens)
            fts_rows = _run_fts_query(con, or_query, scope, limit) or []
            if fts_rows:
                mode = "or_fallback"
        vec_rows = _vector_search(con, query, scope, limit, wait_for_model) if use_semantic else None
    finally:
        con.close()

    if semantic is True:
        rows = (vec_rows or [])[:limit]
        mode = "vector" if vec_rows else "vector_unavailable"
        if not rows:  # nothing embedded / model still loading -> lexical
            rows, mode = fts_rows[:limit], "lexical_fallback"
    elif use_semantic and vec_rows:
        # Reciprocal-rank fusion of the two ranked lists.
        RRF_K = 60
        score, keep = {}, {}
        for rank, r in enumerate(fts_rows):
            score[r["id"]] = score.get(r["id"], 0) + 1.0 / (RRF_K + rank)
            keep[r["id"]] = r
        for rank, r in enumerate(vec_rows):
            score[r["id"]] = score.get(r["id"], 0) + 1.0 / (RRF_K + rank)
            keep.setdefault(r["id"], r)
        rows = [keep[i] for i in sorted(score, key=lambda i: -score[i])][:limit]
        mode = "hybrid"
    else:
        rows = fts_rows[:limit]

    if debug:
        for r in rows:
            r["_search_mode"] = mode
        return {"results": rows, "mode": mode, "and_query": and_query,
                "or_query": or_query, "semantic_available": semantic_available()}
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


def list_entries(scope=None, type_=None, archived=False, limit=100):
    """Browse entries by scope / type / archived state (no search query).

    archived: False = active only (default), True = archived only, None = both.
    """
    limit = _clamp_limit(limit)
    clauses, params = [], []
    if scope:
        clauses.append("scope = ?")
        params.append(scope)
    if type_:
        if type_ not in VALID_TYPES:
            raise ValidationError(f"type must be one of {sorted(VALID_TYPES)}, got {type_!r}")
        clauses.append("type = ?")
        params.append(type_)
    if archived is True:
        clauses.append("archived_at IS NOT NULL")
    elif archived is False:
        clauses.append("archived_at IS NULL")
    sql = ("SELECT id, scope, type, name, description, updated_at, archived_at "
           "FROM entries")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY scope, name LIMIT ?"
    params.append(limit)
    con = connect()
    try:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def prune_events(older_than_days=180):
    """Trim the append-only audit log. Returns the number of rows removed.
    Refuses a window under 30 days, and records the prune itself as an event."""
    if older_than_days < 30:
        raise ValidationError("prune window must be at least 30 days")
    con = connect()
    try:
        cutoff = datetime.now(timezone.utc).timestamp() - older_than_days * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat()
        cur = con.execute("DELETE FROM memory_events WHERE occurred_at < ?", (cutoff_iso,))
        removed = cur.rowcount
        _event(con, "events_pruned", "cli", "terminal", outcome="ok",
               details={"removed": removed, "older_than_days": older_than_days})
        con.commit()
        return removed
    finally:
        con.close()


def sync_from_markdown(root=None, redact_section_fn=None, parse_fn=None, verbose=True):
    """Incremental re-import of a Markdown memory tree.

    Only files whose recorded mtime (`entries.source_mtime`) differs from the
    on-disk mtime are re-read — and even then, if the (redacted) content is
    byte-identical to what's stored, only the mtime marker is bumped (no FTS
    churn, no event). A routine run that changed nothing touches no rows.
    Commits in chunks so a long run doesn't hold one write lock the whole time.

    `parse_fn(path) -> {name,type,description,content}|None` and
    `redact_section_fn(text) -> (text, changed)` are injected by import_md.py so
    this module stays dependency-free and layout-agnostic.
    """
    from pathlib import Path as _P
    root = _P(root) if root else (_P.home() / ".claude" / "projects")
    if parse_fn is None:
        raise ValidationError("sync_from_markdown needs a parse_fn")
    if not root.is_dir():
        return {"ok": False, "error": f"root not found: {root}",
                "imported": 0, "unchanged": 0, "skipped": 0}
    imported = skipped = unchanged = touched = 0
    pending = 0
    con = connect()
    try:
        con.row_factory = sqlite3.Row
        known = {}
        for r in con.execute("SELECT scope, name, source_mtime, content FROM entries"):
            known[(r["scope"], r["name"])] = (r["source_mtime"], r["content"])
        for project_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            memory_dir = project_dir / "memory"
            if not memory_dir.is_dir():
                continue
            scope = project_dir.name
            for md_file in sorted(memory_dir.glob("*.md")):
                if md_file.name.upper() == "MEMORY.MD":
                    continue
                if md_file.name.lower().startswith("credentials"):
                    skipped += 1
                    continue
                parsed = parse_fn(md_file)
                if not parsed:
                    skipped += 1
                    continue
                mtime = md_file.stat().st_mtime
                prev = known.get((scope, parsed["name"]))
                # == not >= : re-import on any mtime difference (covers a file
                # restored from a mtime-preserving backup, or a clock that went
                # backwards) instead of trusting "newer wins".
                if prev is not None and prev[0] == mtime:
                    unchanged += 1
                    continue
                content = parsed["content"]
                if redact_section_fn:
                    content, _ = redact_section_fn(content)
                content, _ = redact_secrets(content)
                if prev is not None and prev[1] == content:
                    # Content unchanged, only the mtime moved -> just record it.
                    con.execute("UPDATE entries SET source_mtime=? WHERE scope=? AND name=?",
                                (mtime, scope, parsed["name"]))
                    touched += 1
                    pending += 1
                else:
                    try:
                        add_entry(scope, parsed["type"], parsed["name"], content,
                                  parsed["description"], source_path=str(md_file),
                                  source_mtime=mtime, actor="sync", origin="terminal",
                                  _con=con)
                        imported += 1
                        pending += 1
                        if verbose:
                            print(f"  [{scope}] {parsed['name']}", file=sys.stderr)
                    except ValidationError as e:
                        skipped += 1
                        if verbose:
                            print(f"  SKIP ({e}): {md_file}", file=sys.stderr)
                if pending >= 25:
                    con.commit()
                    pending = 0
        con.commit()
    finally:
        con.close()
    return {"ok": True, "imported": imported, "mtime_only": touched,
            "unchanged": unchanged, "skipped": skipped}


def delete_entry(scope, name):
    """Physical deletion is reserved for healthcheck probes."""
    if not str(scope).startswith(HEALTHCHECK_SCOPE):
        raise ValidationError("physical_delete_forbidden: use archive_entry")
    con = connect()
    try:
        row = con.execute("SELECT id FROM entries WHERE scope=? AND name=?", (scope, name)).fetchone()
        cur = con.execute("DELETE FROM entries WHERE scope=? AND name=?", (scope, name))
        if row:
            try:
                con.execute("DELETE FROM entries_vec WHERE entry_id=?", (row[0],))
            except sqlite3.OperationalError:
                pass  # vec table not present (semantic disabled)
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def embed_backfill(limit=None, batch=64):
    """Compute vector embeddings for entries that don't have a fresh one
    (`embedded_at IS NULL`), in batches. Never runs on the write path — call
    this from a schedule or on demand. No-op if semantic deps aren't installed."""
    if not semantic_available():
        return {"ok": False, "error": "semantic deps not installed (fastembed + sqlite-vec)"}
    con = connect(with_vec=True)
    try:
        if not _ensure_vec_table(con):
            return {"ok": False, "error": "vec extension failed to load"}
        con.row_factory = sqlite3.Row
        sql = ("SELECT id, name, description, content FROM entries "
               "WHERE embedded_at IS NULL AND archived_at IS NULL ORDER BY id")
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = con.execute(sql).fetchall()
        done = 0
        for i in range(0, len(rows), batch):
            chunk = rows[i:i + batch]
            try:
                vectors = _embed_texts(_embed_input(r["name"], r["description"], r["content"]) for r in chunk)
            except Exception as e:
                remaining = con.execute(
                    "SELECT COUNT(*) FROM entries WHERE embedded_at IS NULL AND archived_at IS NULL").fetchone()[0]
                return {"ok": False, "error": f"embedding failed: {e}", "embedded": done, "remaining": remaining}
            ts = now()
            for r, vec in zip(chunk, vectors):
                blob = _vec_to_blob(vec)
                con.execute("DELETE FROM entries_vec WHERE entry_id=?", (r["id"],))
                con.execute("INSERT INTO entries_vec(entry_id, embedding) VALUES (?, ?)", (r["id"], blob))
                con.execute("UPDATE entries SET embedded_at=? WHERE id=?", (ts, r["id"]))
            con.commit()
            done += len(chunk)
        remaining = con.execute(
            "SELECT COUNT(*) FROM entries WHERE embedded_at IS NULL AND archived_at IS NULL").fetchone()[0]
        return {"ok": True, "embedded": done, "remaining": remaining}
    finally:
        con.close()


def _vec_to_blob(vec):
    import struct
    return struct.pack(f"<{len(vec)}f", *vec)  # sqlite-vec expects little-endian float32


def embed_status():
    con = connect()
    try:
        total = con.execute("SELECT COUNT(*) FROM entries WHERE archived_at IS NULL").fetchone()[0]
        embedded = con.execute(
            "SELECT COUNT(*) FROM entries WHERE embedded_at IS NOT NULL AND archived_at IS NULL").fetchone()[0]
    finally:
        con.close()
    return {"semantic_available": semantic_available(), "model": SEMANTIC_MODEL,
            "entries": total, "embedded": embedded, "stale_or_missing": total - embedded}


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
        hits = search("healthcheck alpha", scope=scope, limit=5, semantic=False)
        record("search_and", any(h["name"] == HEALTHCHECK_NAME for h in hits), f"{len(hits)} hits")
    except Exception as e:
        record("search_and", False, e)

    try:
        # One real term + one term guaranteed absent from the probe content
        # -> strict AND must return 0, forcing the OR-fallback path.
        dbg = search("healthcheck zzznotarealword", scope=scope, limit=5, debug=True, semantic=False)
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
            h["name"] == HEALTHCHECK_NAME for h in search("healthcheck alpha", scope=scope, limit=5, semantic=False)
        )
        restored = restore_entry(scope, HEALTHCHECK_NAME, "healthcheck restore",
                                 actor=actor, origin=origin, session_ref=session_ref)
        visible = get_entry(scope, HEALTHCHECK_NAME) is not None
        record("archive_restore", archived and hidden and durable and absent_from_search and restored and visible)
    except Exception as e:
        record("archive_restore", False, e)

    try:
        probe = (
            "note avant ghp_" + ("A" * 36) + " et\n"
            "password: PhrasePasseSansAucunChiffre\n"
            "-----BEGIN OPENSSH PRIVATE KEY-----\n" + ("b64line" * 8) + "\n\n"
            "fin de note"
        )
        meta = add_entry(scope, "reference", "guard-probe", probe,
                         description="probe", actor=actor, origin=origin,
                         session_ref=session_ref, return_meta=True)
        stored = get_entry(scope, "guard-probe", include_archived=True)
        c = stored["content"] if stored else ""
        codes = set(meta.get("redacted", []))
        redacted_ok = (
            {"github_token", "labelled_secret_line", "private_key"} <= codes
            and "ghp_" not in c
            and "PhrasePasseSansAucunChiffre" not in c   # digit-free value still caught
            and "b64lineb64line" not in c                # key body stripped even without -----END-----
            and "note avant" in c and "fin de note" in c  # surrounding text kept
        )
        record("secret_guard_redacts", redacted_ok, f"redacted={sorted(codes)}")
        delete_entry(scope, "guard-probe")
    except Exception as e:
        record("secret_guard_redacts", False, e)

    try:
        deleted = delete_entry(scope, HEALTHCHECK_NAME)
        record("delete", deleted)
    except Exception as e:
        record("delete", False, e)

    return {"ok": ok, "checks": checks, "db_path": str(DB_PATH)}


def version_info():
    """Local, offline. What this checkout is."""
    return {
        "memcore": __version__,
        "schema": SCHEMA_VERSION,
        "python": ".".join(str(n) for n in sys.version_info[:3]),
    }


def _parse_semver(tag):
    """'v0.2.0' / '0.2.0' -> (0, 2, 0). Non-numeric parts drop to 0."""
    parts = tag.lstrip("vV").split(".")[:3]
    out = []
    for part in parts:
        num = ""
        for ch in part:
            if ch.isdigit():
                num += ch
            else:
                break
        out.append(int(num) if num else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def check_updates(timeout=5):
    """The ONLY command that makes a network call. Asks GitHub for the latest
    release tag and compares it to this checkout. No token, no telemetry sent —
    a plain unauthenticated GET. Never run automatically."""
    import urllib.error
    import urllib.request

    url = f"https://api.github.com/repos/{REPO}/releases/latest"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"MemCore/{__version__}",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"ok": True, "current": __version__, "latest": None,
                    "note": "no published release yet"}
        return {"ok": False, "error": f"HTTP {e.code}", "current": __version__}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "current": __version__}

    latest = data.get("tag_name") or data.get("name") or ""
    behind = _parse_semver(latest) > _parse_semver(__version__)
    return {
        "ok": True,
        "current": __version__,
        "latest": latest,
        "update_available": behind,
        "url": data.get("html_url"),
        "hint": "cd MemCore && git pull  (the DB migrates itself on next run)" if behind else None,
    }


def main():
    p = argparse.ArgumentParser(description="MemCore CLI")
    p.add_argument("--version", "-V", action="version", version=f"MemCore {__version__}")
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
    sp.add_argument("--debug", action="store_true", help="Show which query mode matched plus raw FTS queries")
    sp.add_argument("--semantic", action="store_true", help="Vector search only (loads the model; ~5-15s cold)")
    sp.add_argument("--hybrid", action="store_true", help="Blend FTS + vector (loads the model; ~5-15s cold)")

    sp = sub.add_parser("embed-backfill", help="Compute embeddings for entries missing one (semantic search)")
    sp.add_argument("--limit", type=int, default=None)

    sub.add_parser("embed-status", help="How many entries have a current embedding")

    sp = sub.add_parser("recent")
    sp.add_argument("--scope", default=None)
    sp.add_argument("--limit", type=int, default=20)

    sp = sub.add_parser("list", help="Browse entries by scope/type/archived (no search query)")
    sp.add_argument("--scope", default=None)
    sp.add_argument("--type", default=None, choices=sorted(VALID_TYPES))
    sp.add_argument("--archived", action="store_true", help="Show archived entries instead of active ones")
    sp.add_argument("--all", action="store_true", help="Show both active and archived")
    sp.add_argument("--limit", type=int, default=100)

    sp = sub.add_parser("sync", help="Incremental re-import of ~/.claude/projects/*/memory/*.md (only changed files)")
    sp.add_argument("--root", default=None)
    sp.add_argument("--no-embed", action="store_true", help="Skip the semantic embed-backfill afterwards")

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
    sp.add_argument("--prune-older-than-days", type=int, default=None,
                    help="Instead of listing, delete audit rows older than N days")

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
    sub.add_parser("version", help="Print this checkout's version + schema (offline)")
    sp = sub.add_parser("check-updates",
                        help="Ask GitHub for the latest release (the only command that hits the network)")
    sp.add_argument("--timeout", type=float, default=5.0)

    args = p.parse_args()

    if args.cmd == "init":
        connect().close()
        print(json.dumps({"ok": True, "db_path": str(DB_PATH)}))
    elif args.cmd == "add":
        content = args.content if args.content is not None else sys.stdin.read()
        try:
            meta = add_entry(
                args.scope, args.type, args.name, content, args.description, args.source_path,
                args.expected_updated_at, args.actor, args.origin, args.session_ref,
                return_meta=True,
            )
        except ValidationError as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            return 1
        out = {"ok": True, "id": meta["id"]}
        if meta["redacted"]:
            out["redacted"] = meta["redacted"]
        print(json.dumps(out))
    elif args.cmd == "list":
        archived = None if args.all else (True if args.archived else False)
        try:
            print(json.dumps(list_entries(args.scope, args.type, archived, args.limit),
                             ensure_ascii=False, indent=2))
        except ValidationError as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            return 1
    elif args.cmd == "sync":
        sys.path.insert(0, str(Path(__file__).parent))
        import import_md  # noqa
        result = sync_from_markdown(
            root=args.root,
            redact_section_fn=import_md.redact_secrets,
            parse_fn=import_md.parse_memory_file,
        )
        # Keep the semantic index in step with the files (best effort). This is
        # the natural cadence — "refresh from disk" — so embeddings don't rot
        # between manual runs. No-op if the deps aren't installed.
        if not args.no_embed and result.get("ok") and semantic_available():
            result["embed"] = embed_backfill()
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("ok") else 1
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
        if args.prune_older_than_days is not None:
            try:
                removed = prune_events(args.prune_older_than_days)
            except ValidationError as e:
                print(json.dumps({"ok": False, "error": str(e)}))
                return 1
            print(json.dumps({"ok": True, "pruned": removed}))
        else:
            print(json.dumps(get_events(args.scope, args.name, args.limit), ensure_ascii=False, indent=2))
    elif args.cmd == "backup":
        dest = backup(args.dest)
        print(json.dumps({"ok": True, "dest": dest}))
    elif args.cmd == "history":
        print(json.dumps(get_history(args.scope, args.name, args.limit), ensure_ascii=False, indent=2))
    elif args.cmd == "search":
        # A one-shot CLI process can't benefit from a background model load
        # (it exits first), so the semantic layer is opt-in here and blocks on
        # the model when asked. `--semantic` = vector only, `--hybrid` = both.
        sem = True if args.semantic else (None if args.hybrid else False)
        try:
            print(json.dumps(
                search(args.query, args.scope, args.limit, debug=args.debug,
                       semantic=sem, wait_for_model=(sem is not False)),
                ensure_ascii=False, indent=2))
        except ValidationError as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            return 1
    elif args.cmd == "embed-backfill":
        r = embed_backfill(limit=args.limit)
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r.get("ok") else 1
    elif args.cmd == "embed-status":
        print(json.dumps(embed_status(), ensure_ascii=False, indent=2))
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
    elif args.cmd == "version":
        print(json.dumps(version_info(), ensure_ascii=False, indent=2))
    elif args.cmd == "check-updates":
        result = check_updates(timeout=args.timeout)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
