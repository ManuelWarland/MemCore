# MemCore

> Local, file-based **shared long-term memory** for AI coding assistants.
> One SQLite file. No daemon, no background capture, no cloud, no telemetry.

MemCore gives Claude Code, Codex CLI, Kimi Code, OpenCode, AgentRoom — or any tool
that speaks [MCP](https://modelcontextprotocol.io) or can run a script — a single
persistent memory: the facts, decisions and preferences that should survive across
sessions and be searchable by **every** assistant working on the same projects.

The problem it solves: each AI session starts blank. You re-explain the same
constraints, the same "we already tried that", the same "no, use X not Y". MemCore
is where those get written once and found again — by you or by the next assistant.

---

## Features

| | |
|---|---|
| **Local & private** | A single `memcore.db` (standard SQLite). Readable by any tool. Nothing leaves the machine. |
| **Multi-client** | MCP server, CLI, line-delimited JSON bridge, or raw SQLite — all read/write the same store. |
| **Full-text search** | SQLite FTS5 across every scope. Multi-word queries try strict AND, then fall back to ranked OR — one missing word never zeroes the result. |
| **Provenance & audit** | Every create / update / conflict / refusal / archive / restore is appended to `memory_events` with actor + origin + optional session id. |
| **Safe concurrency** | Optimistic locking via `expected_updated_at`. Concurrent writers get a `conflict` — nothing is silently overwritten. |
| **Reversible deletes** | Archive (soft-delete) → restore. Overwritten versions kept in history. |
| **Per-connection access control** | `--readonly` and/or `--scope <name>` sandboxing, **enforced server-side** (a locked connection cannot escape its scope even if it asks). |
| **Secret hygiene** | Secret-shaped values (API keys, tokens, `password: …` lines) are **redacted** on write — the note is kept, the value stripped, the redaction flagged and audited. `credentials_*` files are skipped from import by filename. |
| **Incremental sync** | `memcore.py sync` re-imports only the Markdown files whose mtime changed since last time — a run that changed nothing touches the DB zero times. |
| **Zero dependencies** | Python 3.11+ standard library only. No `pip install`. |

---

## Install

```bash
git clone https://github.com/AngwattRider/MemCore.git
cd MemCore
python scripts/memcore.py init          # creates the database
python scripts/memcore.py healthcheck   # end-to-end self-test (~1s)
```

**Database location** — defaults to `~/MemCore/memcore.db`. Override with the
`MEMCORE_DB_PATH` environment variable (point it wherever you like — a synced
folder, an encrypted volume, a project directory).

`memcore.db` is **git-ignored** — the code is shareable, your memories are not.

---

## Use it — as a human (CLI)

Works everywhere, no MCP support required. Everything prints JSON on stdout.

```bash
python scripts/memcore.py search "telegram rate limit"
python scripts/memcore.py recent
python scripts/memcore.py list --scope my-project           # browse a scope
python scripts/memcore.py list --type feedback              # all feedback entries
python scripts/memcore.py list --archived                   # what's archived
python scripts/memcore.py scopes
python scripts/memcore.py stats

python scripts/memcore.py add \
  --scope "my-project" \
  --type  "feedback" \
  --name  "prefer-tabs-over-spaces" \
  --description "One-line summary used for recall ranking" \
  --content "The full note."

python scripts/memcore.py sync                              # incremental .md -> DB re-import
python scripts/memcore.py backup [--dest PATH]              # consistent copy (SQLite backup API)
python scripts/memcore.py events [--scope X] [--prune-older-than-days 180]
```

`type` is one of `user` / `feedback` / `project` / `reference` (see
[Conventions](#conventions)). Upsert is automatic on `scope` + `name`.

---

## Use it — as an AI assistant (MCP)

Add MemCore as a local MCP server. **The exact config shape differs per host** —
here are ones verified working:

| Host | Config file | Root key | `command` shape |
|---|---|---|---|
| Claude Code | `~/.claude.json` | `mcpServers` | `command` (string) + `args` (array) + `type: "stdio"` |
| Codex CLI | `~/.codex/config.toml` | `[mcp_servers.memcore]` (TOML) | `command` + `args` |
| Kimi Code CLI | `~/.kimi-code/mcp.json` | `mcpServers` | same as Claude Code |
| OpenCode | `~/.config/opencode/opencode.jsonc` | `mcp` (not `mcpServers`) | `command` = **one array** combining interpreter + script, plus `type: "local"` and `enabled: true` |

Claude Code example:

```json
{
  "mcpServers": {
    "memcore": {
      "type": "stdio",
      "command": "python",
      "args": ["/absolute/path/to/MemCore/scripts/memcore_mcp.py",
               "--actor", "claude", "--origin", "terminal"]
    }
  }
}
```

> Most CLIs only read their MCP config at startup — **restart the tool** (new
> session) after editing it before concluding something is broken.

### Access profiles (per connection)

Set on the connection's own `args`, not globally:

| Profile | Args | Effect |
|---|---|---|
| Full trust | *(none)* | Read/write, all scopes |
| Read-only | `--readonly` | Sees everything, cannot overwrite/delete |
| Sandbox | `--scope <name>` | Read/write limited to one scope, even if another is requested |
| Most restrictive | `--readonly --scope <name>` | Read-only view of a single scope |

Scope locking is enforced server-side and covered by an adversarial test
(`scripts/test_access_profiles.py`). Default recommendation: start any unproven
assistant read-only (or sandboxed), widen only after trust.

### MCP tools

| Tool | Params | Purpose |
|---|---|---|
| `memory_search` | `query`, `scope?`, `limit?`, `debug?` | Full-text search across all scopes (AND→OR fallback). `debug=true` returns the matched mode + raw queries. |
| `memory_write` | `scope`, `type`, `name`, `content`, `description?`, `expected_updated_at?` | Create or update. Pass `expected_updated_at` to guard against concurrent overwrites. |
| `memory_get` | `scope`, `name` | One entry. |
| `memory_list` | `scope?`, `type?`, `archived?`, `limit?` | Browse entries without a query — everything in a scope, all of one type, or what's archived. |
| `memory_recent` | `scope?`, `limit?` | Most recently modified entries. |
| `memory_events` | `scope?`, `name?`, `limit?` | Read the append-only audit log (writes, conflicts, redactions). |
| `memory_history` | `scope`, `name`, `limit?` | Previous versions of an overwritten/deleted entry. |
| `memory_archive` / `memory_restore` | `scope`, `name`, `reason` | Reversible soft-delete / undelete. |
| `memory_scopes` | — | Scopes + entry counts. |
| `memory_stats` | — | Totals + DB path. |
| `memory_healthcheck` | — | End-to-end self-test on a throwaway entry. Use before trusting a connection — search can misbehave silently while the connection looks up. |

**Safe writes**: before updating an existing entry, `memory_get` it and pass its
`updated_at` back as `expected_updated_at`. If another client changed it meanwhile,
the write is refused with `conflict` and nothing is lost.

**Provenance**: launch with `--actor <name> --origin <terminal|agentroom|...>` and,
if available, `--session-ref <id>`. Identity is fixed at server start, not chosen
per call.

---

## For an AI reading this repository for the first time

This is a persistent memory store shared across the AI tools used on this machine.
It holds established facts, prior decisions and user preferences.

1. **Search before you ask.** `memory_search "<topic>"` (or the CLI). It spans every
   scope — you don't need to know which one a fact lives in.
2. **Write what should outlive this conversation** — a corrected assumption, a
   design decision, a "we already ruled this out". Not conversational noise.
3. **Don't rewrite an entry that's already present and still correct.**
4. If a `global`-scope profile exists, read it first (`memory_search "user profile"`,
   `memory_search "collaboration rules"`) — it's how the person you're working with
   wants assistants to behave.

---

## Other access methods

### Line-delimited JSON bridge

`scripts/memcore_bridge.py` — one JSON request per line, one JSON response per line.
For local orchestrators (e.g. AgentRoom). No npm, no raw SQLite.

```bash
python scripts/memcore_bridge.py --actor codex --origin agentroom --session-ref "room:1/run:42"
# stdin:  {"op":"memory_search","query":"deploy steps","limit":5}
```

### Raw SQLite — maintenance only

```sql
CREATE TABLE entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    type  TEXT NOT NULL,          -- 'user' | 'feedback' | 'project' | 'reference'
    name  TEXT NOT NULL,          -- slug, unique within its scope
    description TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    source_path TEXT,             -- origin .md path if imported, else NULL
    created_at TEXT NOT NULL,     -- ISO 8601 UTC
    updated_at TEXT NOT NULL,
    UNIQUE(scope, name)
);
-- entries_fts (FTS5) is kept in sync by triggers. Never write to it directly.
```

```sql
SELECT e.* FROM entries_fts JOIN entries e ON e.id = entries_fts.rowid
WHERE entries_fts MATCH 'your terms' ORDER BY bm25(entries_fts) LIMIT 20;
```

Direct SQL bypasses validation and `memory_events` — use MCP / CLI / bridge for
anything an agent does; keep raw SQL for human maintenance and restoration.

### Markdown import (optional helpers)

`scripts/import_md.py` and `scripts/import_claude_md.py` bulk-import an existing
tree of Markdown memory files (the layout Claude Code writes under
`.claude/projects/*/memory/`) and a `CLAUDE.md` profile. Both redact common secret
patterns and skip `credentials_*` files. Re-runnable — they upsert on `scope` + `name`.

---

## Conventions

- **`scope`** — the project or topic (a project name, or `global` for facts true
  everywhere). Consistency is nice but not critical: search spans all scopes.
- **`name`** — short kebab-case slug, unique within its scope. Reuse it to update.
- **`type`** — `user` (who the person is / their preferences), `feedback` (a lesson
  or correction they gave), `project` (a fact/state about ongoing work),
  `reference` (a pointer to something external).
- Store only what should survive the current conversation.

Validation on every write: `type` must be valid, `content` non-empty and ≤ 200 000
chars, `scope`/`name`/`description` ≤ 500 chars. Invalid writes return
`{"ok": false, "error": "..."}` — the store is never corrupted.

---

## Secrets

Two layers:

1. **`credentials_*.md` files** in a Markdown import tree are **excluded by
   filename** — never imported.
2. **Everything else is redacted, not rejected.** On any write, secret-shaped
   substrings (private keys, `ghp_…` / `github_pat_…`, `AKIA…`, `sk-…` /
   `sk-ant-…`, `AIza…`, Telegram bot tokens, JWTs, `password: <value>` /
   `api_key = <value>` lines) are replaced with `[REDACTED]`. The surrounding
   note is kept, the redaction is returned to the caller (`"redacted": [...]`)
   and logged to `memory_events`. A note that merely *discusses* a secret format
   is fine; a real leaked value is stripped before it hits disk.

If an assistant needs an actual credential, it should ask the user — not look here.

---

## Backup & restore

`python scripts/memcore.py backup [--dest PATH]` makes a consistent copy (SQLite
backup API — safe even under concurrent writes). Point `--dest` (or
`MEMCORE_BACKUP_PATH`) into a folder that is itself backed up.

Restore, worst case first:

- **DB corrupt, a backup copy exists** → stop every tool using MemCore, replace
  `memcore.db` with the backup, run `memcore.py healthcheck`.
- **No DB, but the source `.md` files exist** → `import_claude_md.py` then
  `import_md.py` rebuild the index. Lost in this case: `memory_history`,
  `memory_events`, archived entries — the live content of every entry that has a
  `.md` comes back.
- **DB fine, MCP silent** → `memcore.py healthcheck` (if `ok`, it's the MCP layer):
  fully restart the host, check its `mcpServers.memcore` entry. The CLI works
  without MCP in the meantime.

---

## Design notes

No daemon. No automatic background capture. No mandatory AI summarization. Just
reliable storage + search, plus the guardrails (provenance, concurrency,
reversible deletes, access control) that make it safe for several assistants to
share one store. Markdown files can live alongside it as the human-readable
source of truth; MemCore is a faster, more widely reachable index of them.

## Credits

Designed and written by **Claude** (Anthropic), directed by **Manuel Warland**
([@AngwattRider](https://github.com/AngwattRider)), who maintains it.

## License

[MIT](LICENSE) — Copyright (c) 2026 Manuel Warland.
