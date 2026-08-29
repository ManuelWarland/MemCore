# MemCore

### Your AI finally remembers.

A single local database of the things worth keeping — decisions, corrections,
"we already tried that" — shared by **every** AI tool you use, searchable in
plain language, across every project.

No daemon. No cloud. No telemetry. One SQLite file you own.

🇫🇷 [Version française](README.fr.md)

---

## You've had this conversation

You're deep in a project with an AI agent. It's flying. Then the context window
fills, or you close the terminal, or it's simply tomorrow — and you're back to
square one with someone who has *no idea who you are*.

You re-explain the constraints. You re-explain, again, that *"we already tried
the buffered approach and it deadlocks."* You spent an hour last Tuesday
arguing through a design trade-off; today the agent proposes the exact option
you threw out.

And then it gets worse — it starts making things up:

> **You:** Remember in the payments project, when we fixed the double-charge
> bug? What did we actually change?
>
> **Agent:** Yes — we added an idempotency key on the request, moved the retry
> logic behind a mutex, and set the timeout to 8 seconds.

None of that happened. You're not even in the payments repo right now. The
agent had nothing real to work from, so it produced something that *sounds*
right — and you only catch it because *you* remember. Most days, you don't.

Now multiply that by every tool on your machine. One agent doesn't know what
you told another. Each wakes up blank, every single time. **You** have become
the memory. That's the bug.

## MemCore fixes it

MemCore is one small local database of **durable facts** — the decisions, the
corrections, the "here's how this actually works", the "we ruled this out and
here's why". Every AI tool you run reads and writes the *same* store.

Ask before you re-explain:

```
memory_search "why did we drop the queue-based design"
```

> `project` note, from three weeks ago: *"queue design deadlocked under load on
> 2026-08-02, reverted to direct calls."* — written in a different repo, found
> anyway.

And when there's nothing to find, it says so — a plain *no match*, not a
confident answer improvised to fill the silence.

Corrections that finally stick. Tell it once that the API returns cents, not
dollars; write it down; the next session — and the next tool — starts already
knowing.

A paper trail. Every write is logged with who and when. Overwrites keep the old
version. Nothing is ever silently lost.

## What it feels like

**Coming back:**

> **You:** back — where were we?
>
> *(agent: `memory_recent`, `memory_search`)*
>
> **Agent:** Last real change, 40 minutes ago: v1.0.84 shipped, replacement
> parts ordered, waiting on delivery. You were about to start the user guide.
> Nothing else moved.

Not a guess. Not a stale summary. The actual state, reconstructed from what was
recorded.

**The cross-project question — answered straight:**

> **You:** we standardised on the EU date format everywhere, right?
>
> **Agent:** *(`memory_search "date format"`)* Yes — `feedback`, global scope,
> still current. And there's a follow-up note: the CSV export is the one
> exception, it stays ISO-8601.

**Onboarding a fresh agent to a codebase:**

```
memory_list --scope my-project --type feedback
```

> Every lesson the last ten sessions learned about this repo, in one list —
> before it writes a single line.

**Not re-litigating a settled decision:** the agent starts to suggest an
approach, checks MemCore first, finds the note where that approach already
failed, and suggests something else. You never even see the wrong turn.

**Closing out:**

> **You:** memcorise all of this.
>
> *(agent writes the session's durable facts — what shipped, what's still open,
> what we decided — into MemCore)*

> ***memcorise*** *(verb): to write it down once, so no AI ever has to ask again.*

## Why the name

The **core** of memory. Not a transcript, not a log, not "everything the model
ever saw" — the small, curated, durable part. The handful of facts that, if you
lost them, you'd have to painfully earn back.

## The one design decision that matters

**Nothing is captured automatically.** No background hooks, no daemon watching
your session. An AI writes to MemCore the way it writes a note to itself —
deliberately, when something is actually worth keeping. That single choice is
why it's small, fast, and doesn't break: there's almost nothing to go wrong.

## What it won't do

MemCore gives back the text that was written, with its scope, its date, its
full edit history. Ask for something nobody ever recorded and you get *no
match*: an honest blank, not a plausible answer improvised on the spot. The
failure mode is "nothing found", which you notice, rather than "sounds right",
which you don't.

It doesn't go further than that. It won't judge whether a stored fact is still
*true* or *current* — that's on whoever wrote it, which is why the writing
discipline further down is strict. And an assistant can still misread a result
it did retrieve. What MemCore removes is the one failure you can't catch on your
own: an AI filling a gap in its knowledge with invention.

---

## Features

Works with Claude Code, Codex CLI, Kimi Code, OpenCode, AgentRoom — or any tool
that speaks [MCP](https://modelcontextprotocol.io) or can run a script.

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
| **Semantic search (optional)** | Install `sqlite-vec` + `fastembed` and MemCore blends FTS with vector nearest-neighbours on a multilingual sentence model — finds entries about the same idea with no shared keywords. Embeddings are computed off the write path (`embed-backfill`). Not installed → lexical only, zero deps. |
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

**Semantic search (optional)** — `pip install -r requirements-semantic.txt`
then `python scripts/memcore.py embed-backfill`. Adds `sqlite-vec` (a small C
extension) and `fastembed` (ONNX, no PyTorch). The default model is
`paraphrase-multilingual-mpnet-base-v2` (~1 GB, downloaded once); override with
`MEMCORE_EMBED_MODEL`. Turn it off without uninstalling: `MEMCORE_SEMANTIC=0`.

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

# Semantic search (needs: pip install -r requirements-semantic.txt)
python scripts/memcore.py embed-backfill                    # embed entries missing a vector
python scripts/memcore.py embed-status
python scripts/memcore.py search "how do I back things up" --hybrid    # FTS + vector (loads the model, ~5-15s cold)
python scripts/memcore.py search "how do I back things up" --semantic  # vector only
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
It holds established facts, prior decisions and user preferences. If a `global`
scope exists, read it first (`memory_search "user profile"`,
`memory_search "collaboration rules"`) — it's how the person you're working with
wants assistants to behave. Then follow the discipline below.

---

## Using it well

### Reading — search first, by default

Search **before** you:

- say *"I don't know"* or *"there's no record of that"*
- ask a question the user may already have answered
- propose an approach — has it been tried and rejected?
- confirm or correct someone from memory
- treat a surprising result as a mystery

At the start of a session: `memory_recent` plus a targeted `memory_search` to
rebuild the real state — don't guess, don't summarise from a stale mental model.
Search is cheap. The cost of *not* searching is the confident-lie failure this
tool exists to kill.

### Writing — only what you'd hate to re-earn

Write when:

- a **decision** was made — especially after a trade-off or a debate
- a **correction** landed — *"no, it's cents, not dollars"*
- a **dead end** was hit — *"tried the queue design, deadlocked under load, reverted"*
- you learned **how the setup actually works** and it isn't in the code or docs
- the user stated a **preference**

Don't write: a step-by-step of what you just did, anything git or the code
already records, anything that only matters for this conversation, "reminders"
to yourself. Write **when the fact crystallises**, not in a batch at the end —
you'll forget half of it.

### How to write one

- **One fact per entry.** Tight kebab-case `name`. A `description` that says what
  the fact *is* — it drives recall ranking — not "notes about X".
- Pick the `type` honestly: `user` / `feedback` / `project` / `reference`.
- For `feedback` and `project`: include **why it matters** and **how to apply
  it**. A rule with no rationale gets misapplied or ignored.
- Relative dates → absolute ("last Tuesday" → the actual date).
- **Update** the existing entry (same `scope` + `name`) — don't create a near-twin.
- If it turns out wrong, **archive or delete it**. A stale fact is worse than none.

### Trusting what you read

- A recalled memory is **background context, not a fresh instruction** — it's
  what was true *when it was written*.
- If it names a file, function or flag: **check it still exists** before acting on it.
- A *"not done yet / pending"* fact is **perishable** — re-verify it, don't build on it.
- *"the user chose X"* — was it their independent call, or your suggestion they
  accepted? Don't misattribute a decision.

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
   substrings are replaced with `[REDACTED]`: private keys (even a truncated
   paste), `ghp_…` / `github_pat_…`, `AKIA…`, `sk-…` / `sk-ant-…`, `AIza…`,
   `xox[baprs]-…`, Telegram bot tokens, JWTs, `scheme://user:pass@host`
   connection strings, `Authorization: Bearer …` headers, and `password:` /
   `api_key =` lines (a lone value, no path/URL). The surrounding note is kept,
   the redaction is returned to the caller (`"redacted": [codes]`) and logged to
   `memory_events`. A note that merely *discusses* a secret format is fine; a
   real leaked value is stripped before it hits disk.

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
