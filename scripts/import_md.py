#!/usr/bin/env python3
"""One-shot / re-runnable importer: memory/*.md (all project scopes) -> MemCore.

Idempotent: re-running re-imports the current file content (UNIQUE(scope, name)
upsert in memcore.add_entry), so this can be run again any time the .md files
change, or on a schedule.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import memcore  # noqa: E402

PROJECTS_ROOT = Path.home() / ".claude" / "projects"

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
NAME_RE = re.compile(r'^name:\s*"?([^"\n]+?)"?\s*$', re.MULTILINE)
DESC_RE = re.compile(r'^description:\s*"?(.+?)"?\s*$', re.MULTILINE)
TYPE_RE = re.compile(r'^\s*type:\s*"?([a-zA-Z_-]+)"?\s*$', re.MULTILINE)

# Defense in depth for secrets that leak OUTSIDE a credentials_*.md file (e.g. an
# inline "## Credentials" section in an ordinary project note). Filename-based
# exclusion alone is not enough, as found in this repo's own memory files.
CREDENTIALS_SECTION_RE = re.compile(
    r"^#{1,4}\s*(credentials?|identifiants?|secrets?|mots?\s*de\s*passe)\b.*?(?=^#{1,4}\s|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),  # GitHub PAT / OAuth / user-to-server / refresh / server tokens
    re.compile(r"github_pat_[A-Za-z0-9_]{30,}"),  # GitHub fine-grained PAT
    re.compile(r"\d{8,10}:[A-Za-z0-9_-]{30,}"),  # Telegram bot token
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key ID
    re.compile(r"sk-(ant-)?[A-Za-z0-9_-]{20,}"),  # OpenAI/Anthropic-style API key
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),  # Google API key
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),  # JWT
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),  # SSH/PGP key
    re.compile(r"(?im)^.*\b(mot de passe|password|psk|cl[ée]\s*(wifi|api|secr[èe]te?))\s*[:\`]\s*\S{6,}.*$"),  # labelled password lines
]


def redact_secrets(text: str):
    redacted = False
    stripped, n = CREDENTIALS_SECTION_RE.subn("[REDACTED SECTION — credentials, excluded from MemCore]\n", text)
    if n:
        redacted = True
        text = stripped
    for pattern in SECRET_PATTERNS:
        text, n = pattern.subn("[REDACTED]", text)
        if n:
            redacted = True
    return text, redacted


def parse_memory_file(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    frontmatter, body = m.group(1), m.group(2)
    name_m = NAME_RE.search(frontmatter)
    desc_m = DESC_RE.search(frontmatter)
    type_m = TYPE_RE.search(frontmatter)
    name = name_m.group(1).strip() if name_m else path.stem
    description = desc_m.group(1).strip() if desc_m else ""
    type_ = type_m.group(1).strip() if type_m else "reference"
    return {
        "name": name,
        "description": description,
        "type": type_,
        "content": body.strip(),
    }


def main():
    if not PROJECTS_ROOT.exists():
        print(f"Projects root not found: {PROJECTS_ROOT}", file=sys.stderr)
        return 1

    imported, skipped = 0, 0
    for project_dir in sorted(PROJECTS_ROOT.iterdir()):
        memory_dir = project_dir / "memory"
        if not memory_dir.is_dir():
            continue
        scope = project_dir.name
        for md_file in sorted(memory_dir.glob("*.md")):
            if md_file.name.upper() == "MEMORY.MD":
                continue  # index file, not a standalone entry
            if md_file.name.lower().startswith("credentials"):
                # Secrets stay only in the .md file, protected by the same NTFS
                # permissions but not surfaced by MemCore's cross-AI search/README.
                skipped += 1
                print(f"  skip (credentials, excluded by design): {md_file}", file=sys.stderr)
                continue
            parsed = parse_memory_file(md_file)
            if not parsed:
                skipped += 1
                print(f"  skip (no frontmatter): {md_file}", file=sys.stderr)
                continue
            parsed["content"], was_redacted = redact_secrets(parsed["content"])
            if was_redacted:
                print(f"  REDACTED secret(s) in: {md_file}", file=sys.stderr)
            try:
                memcore.add_entry(
                    scope=scope,
                    type_=parsed["type"],
                    name=parsed["name"],
                    content=parsed["content"],
                    description=parsed["description"],
                    source_path=str(md_file),
                    source_mtime=md_file.stat().st_mtime,
                )
            except memcore.ValidationError as e:
                # A single file that trips a validation rule (secret guard,
                # length cap, ...) must NOT abort the whole re-import — skip it,
                # keep going. Common false positive: a note that legitimately
                # *discusses* secret formats.
                skipped += 1
                print(f"  SKIP ({e}): {md_file}", file=sys.stderr)
                continue
            imported += 1
            print(f"  [{scope}] {parsed['name']}")

    print(f"\nImported/updated: {imported}, skipped: {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
