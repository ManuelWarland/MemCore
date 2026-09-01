# Changelog

All notable changes to MemCore. Dates are ISO-8601.

## [Unreleased]

### Added
- README (EN + FR): "A setup that works well around it" — the LLM-wiki pattern
  MemCore is the search layer of (Markdown vault + IPCRA/PARA classification +
  schema file + append-only log + periodic lint), tool-agnostic, no personal
  layout prescribed.

## [0.2.0] — 2026-08-31

Second pass. Two adversarial code reviews; every finding fixed.

### Added
- `version` / `--version` / `-V` — print this checkout's version + schema number
  (offline). `check-updates` — an **opt-in** command that asks GitHub for the
  latest release tag and compares; it is the *only* command that ever makes a
  network call, never runs automatically, and sends no token or telemetry.
- README (EN + FR): a "What comes back" block with real JSON output, and an
  "Updating" section (`git pull`; the DB self-migrates on next run).
- **Optional semantic search** — install `sqlite-vec` + `fastembed` and `search`
  blends FTS with vector nearest-neighbours on a multilingual sentence model
  (`paraphrase-multilingual-mpnet-base-v2` by default). Embeddings are computed
  off the write path by `embed-backfill`; `sync` chains it. Not installed → FTS
  only, zero dependencies. New: `embed-backfill`, `embed-status` (CLI),
  `memory_embed_status` (MCP), `search --semantic` / `--hybrid`, env vars
  `MEMCORE_EMBED_MODEL` / `MEMCORE_EMBED_DIM` / `MEMCORE_SEMANTIC`.
- `list` / `memory_list` — browse entries by scope / type / archived state,
  no search query.
- `memory_events` exposed over MCP (was CLI-only); `events --prune-older-than-days`.
- `sync` — incremental Markdown re-import: only files whose mtime changed are
  re-read; identical content just bumps a marker (no FTS churn).
- `PRAGMA user_version` schema versioning (v3); `_migrate` is now a no-op on an
  up-to-date database.
- README: narrative intro, `README.fr.md`, a "Using it well" discipline section.

### Changed
- **Secrets are redacted, not rejected.** A write containing a secret-shaped
  value keeps the note, strips the value, flags it in the result and the audit
  log. The name-based check (a slug containing "token"/"secret") is gone.
- `description` over 500 chars is truncated, not rejected.
- `MEMCORE_BACKUP_PATH` default is now `~/MemCore/backups/` (was machine-specific).
- MCP `memory_search` default is hybrid but non-blocking: the first search of a
  session returns lexical results immediately while the model loads in the
  background; later searches use the vector layer.
- `connect()` only loads the embedding stack when a caller explicitly needs it.

### Fixed
- `import_md.py` no longer aborts the whole re-import when one file trips a
  validation rule — it skips that file and continues.
- The JSONL bridge now returns secret-redaction metadata just like the CLI and
  MCP paths, so orchestrators can report that a value was stripped on write.
- MCP subprocess tests propagate `MEMCORE_DB_PATH` (they no longer touch a real
  database) and now assert on behaviour instead of printing.

## [0.1.0] — 2026-08-28

Initial public code. SQLite + FTS5 store, MCP server, CLI, JSONL bridge.
Provenance/audit log, optimistic-concurrency conflicts, reversible archive,
per-connection read-only / single-scope access control, secret hygiene on
Markdown import.
