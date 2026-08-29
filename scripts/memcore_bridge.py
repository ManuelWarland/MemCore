#!/usr/bin/env python3
"""Small JSONL stdio bridge for trusted local orchestrators such as AgentRoom.

One JSON request per stdin line, one JSON response per stdout line. Identity is
bound when the process starts; callers cannot spoof actor/origin in requests.
"""

import argparse
import json
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr, sys.stdin):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))
import memcore  # noqa: E402


def audit_read(operation, actor, origin, session_ref, scope=None, name=None, outcome="ok", reason=None):
    con = memcore.connect()
    try:
        memcore._event(con, operation, actor, origin, session_ref, scope, name,
                       outcome=outcome, reason=reason)
        con.commit()
    finally:
        con.close()


def execute(request, actor, origin, session_ref):
    op = str(request.get("op", ""))
    scope = request.get("scope")
    name = request.get("name")
    if op == "memory_search":
        # This process is spawned per request; loading the embedding model here
        # (~5-15s cold) would dominate. Default to lexical; a caller that wants
        # the semantic blend passes "semantic": true explicitly.
        result = memcore.search(request.get("query", ""), scope, request.get("limit", 20),
                                request.get("debug", False), semantic=request.get("semantic", False))
    elif op == "memory_recent":
        result = memcore.recent(request.get("limit", 20), scope, request.get("include_archived", False))
    elif op == "memory_get":
        result = memcore.get_entry(scope, name, request.get("include_archived", False))
    elif op == "memory_scopes":
        result = memcore.list_scopes(request.get("include_archived", False))
    elif op == "memory_stats":
        result = memcore.stats()
    elif op == "memory_history":
        result = memcore.get_history(scope, name, request.get("limit", 20))
    elif op == "memory_write":
        write_result = memcore.add_entry(
            scope, request.get("type"), name, request.get("content", ""),
            request.get("description", ""), expected_updated_at=request.get("expected_updated_at"),
            actor=actor, origin=origin, session_ref=session_ref,
            return_meta=True,
        )
        result = {"id": write_result["id"]}
        if write_result["redacted"]:
            result["redacted"] = write_result["redacted"]
        return {"ok": True, "result": result}
    elif op == "memory_archive":
        result = memcore.archive_entry(scope, name, request.get("reason"), actor, origin, session_ref)
        return {"ok": result}
    elif op == "memory_restore":
        result = memcore.restore_entry(scope, name, request.get("reason"), actor, origin, session_ref)
        return {"ok": result}
    elif op == "memory_healthcheck":
        result = memcore.healthcheck(actor=actor, origin=origin, session_ref=session_ref)
    else:
        raise memcore.ValidationError(f"unknown_operation: {op}")
    audit_read(op, actor, origin, session_ref, scope, name)
    return {"ok": True, "result": result}


def main():
    parser = argparse.ArgumentParser(description="MemCore JSONL stdio bridge")
    parser.add_argument("--actor", required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--session-ref", default=None)
    args = parser.parse_args()

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise memcore.ValidationError("request must be a JSON object")
            response = execute(request, args.actor, args.origin, args.session_ref)
        except memcore.ConflictError as exc:
            response = {"ok": False, "error_code": "conflict", "error": str(exc)}
        except memcore.ValidationError as exc:
            code = "secret_detected" if str(exc).startswith("secret_detected") else "validation_error"
            response = {"ok": False, "error_code": code, "error": str(exc)}
        except Exception as exc:
            response = {"ok": False, "error_code": "internal_error", "error": type(exc).__name__}
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
