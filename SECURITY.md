# Security policy

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Use GitHub's private reporting:
[**Report a vulnerability**](https://github.com/ManuelWarland/MemCore/security/advisories/new).

If that is unavailable, open a normal issue titled only *"security — contact needed"*
with no details, and a maintainer will arrange a private channel.

This is a personal project maintained in spare time. Expect an acknowledgement
within about a week and a fix on a best-effort basis. Please give a reasonable
window before any public disclosure.

## Scope

MemCore is **local-first**: one SQLite file, no daemon, no network in the base
configuration. Most of the threat surface is about what a *local* process or a
connected AI assistant can do to the store. Reports in these areas are in scope:

- **Scope / read-only lock bypass** — a connection started with `--scope X` and/or
  `--readonly` managing to read or write outside those bounds. This is
  enforced server-side and covered by `scripts/test_access_profiles.py`; a bypass
  is a real bug.
- **Secret-redaction bypass** — a secret-shaped value (API key, token, private
  key, connection string, `Authorization:` header, `password:` line) reaching
  disk unredacted through MCP, the CLI, or the JSONL bridge. Note that
  `import_md.py` / raw SQL are explicitly *not* redaction boundaries (documented).
- **Store corruption** — an input that leaves the database inconsistent or the
  FTS index desynced, rather than being rejected with `{"ok": false}`.
- **Path traversal / unintended file access** via `MEMCORE_DB_PATH`,
  `--source-path`, or the Markdown importers.
- **Injection** into the MCP tool layer that lets a crafted entry or query
  execute unintended SQL or shell.

## Not in scope

- Anything requiring the attacker to already have write access to `memcore.db`
  or the ability to run arbitrary code as your user — at that point the SQLite
  file is theirs anyway.
- The optional semantic-search dependencies (`sqlite-vec`, `fastembed`) and
  their transitive packages — report those upstream.
- Content *written into* MemCore being wrong or malicious: MemCore stores text
  faithfully and does not vet its truth. An assistant reading a poisoned entry
  is a prompt-injection concern for that assistant, not a MemCore vulnerability
  (though redaction gaps that *help* such an attack are in scope).
- A remote/hosted deployment: MemCore has no supported network transport yet.

## Handling secrets in a report

Redact real credentials before sending. If a redaction bypass is the bug itself,
describe the *pattern* that slips through — you do not need to include a live
secret to demonstrate it.
