# Contributing

Small, focused pull requests welcome.

## Ground rules

- **Core stays stdlib-only.** `memcore.py`, `memcore_mcp.py`, `memcore_bridge.py`
  and the importers must run with nothing but the Python standard library plus
  `mcp` (for the server). Anything heavier goes behind an optional-dependency
  guard, the way semantic search does (`requirements-semantic.txt` +
  `semantic_available()`).
- **No background capture.** No daemon, no hooks, no automatic session
  scraping. An AI writes to MemCore deliberately. This is the constraint that
  keeps it from breaking.
- **Nothing on the write path may block or fail slowly.** Embedding, network,
  model loading — all deferred or off to the side.

## Running the tests

```bash
python -m py_compile scripts/*.py
python scripts/test_phase_a.py          # core logic, end to end
python scripts/test_phase_b_clients.py  # MCP server, 3 client identities
python scripts/test_mcp_client.py       # every MCP tool, real stdio client
python scripts/test_access_profiles.py  # read-only / scope-lock enforcement
```

Each prints `*_TESTS_OK` on success and raises `AssertionError` on failure.
They use a throwaway database (`tempfile` + `MEMCORE_DB_PATH`) and set
`MEMCORE_SEMANTIC=0`, so they never touch a real store and don't need the
optional deps. CI runs all four on every push.

## Schema changes

Bump `SCHEMA_VERSION` in `memcore.py` and add the migration step to `_migrate()`
(it's gated on `PRAGMA user_version`). Migrations must be idempotent.

## Style

Match the surrounding code. Comments explain *why*, not *what*. Keep functions
small enough to hold in your head.
