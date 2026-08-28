#!/usr/bin/env python3
"""Import ~/.claude/CLAUDE.md (global instructions/profile/rules) into MemCore.

CLAUDE.md is auto-loaded by Claude Code/Desktop via their own mechanism, but
other MCP clients (Codex CLI, etc.) never see it unless it's also here. Split
into one MemCore entry per section so it stays searchable/scoped rather than
one giant blob.

Idempotent: re-run any time CLAUDE.md changes.
"""

import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import memcore  # noqa: E402

CLAUDE_MD_PATH = Path.home() / ".claude" / "CLAUDE.md"
SCOPE = "global"

SECTION_RE = re.compile(r"^#{1,2}\s+(.+?)\s*$", re.MULTILINE)


def slugify(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:80]


def parse_sections(text):
    matches = list(SECTION_RE.finditer(text))
    sections = []
    for i, m in enumerate(matches):
        heading = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        sections.append((heading, body))
    return sections


def main():
    if not CLAUDE_MD_PATH.exists():
        print(f"CLAUDE.md not found: {CLAUDE_MD_PATH}", file=sys.stderr)
        return 1

    text = CLAUDE_MD_PATH.read_text(encoding="utf-8")
    sections = parse_sections(text)

    imported = 0
    for heading, body in sections:
        if not body:
            continue
        type_ = "user" if "profil de manu" in heading.lower() else "feedback"
        name = "claude-md-" + slugify(heading)
        memcore.add_entry(
            scope=SCOPE,
            type_=type_,
            name=name,
            content=body,
            description=heading,
            source_path=str(CLAUDE_MD_PATH),
        )
        imported += 1
        print(f"  [{SCOPE}] {name}")

    print(f"\nImported/updated: {imported} sections from CLAUDE.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
