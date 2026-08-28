#!/usr/bin/env python3
"""Phase A regression tests. Uses an isolated temporary database."""

import importlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    with tempfile.TemporaryDirectory(prefix="memcore-phase-a-") as folder:
        os.environ["MEMCORE_DB_PATH"] = str(Path(folder) / "test.db")
        import memcore
        importlib.reload(memcore)

        eid = memcore.add_entry("test", "project", "durable", "contenu initial", "test",
                                actor="codex", origin="terminal", session_ref="test:1")
        current = memcore.get_entry("test", "durable")
        require(current and current["id"] == eid, "create/get failed")

        updated = memcore.add_entry(
            "test", "project", "durable", "contenu modifié", "test",
            expected_updated_at=current["updated_at"], actor="claude", origin="terminal",
        )
        require(updated == eid, "safe update changed id")
        try:
            memcore.add_entry("test", "project", "durable", "stale", "test",
                              expected_updated_at=current["updated_at"], actor="kimi", origin="terminal")
            raise AssertionError("stale update was accepted")
        except memcore.ConflictError:
            pass
        require(memcore.get_entry("test", "durable")["content"] == "contenu modifié", "conflict changed content")

        require(memcore.archive_entry("test", "durable", "test archive", "codex", "terminal"), "archive failed")
        require(memcore.get_entry("test", "durable") is None, "archived entry still visible")
        require(memcore.get_entry("test", "durable", True)["archived_at"], "archived entry not durable")
        require(not memcore.search("contenu modifié", "test"), "archived entry still searchable")
        require(memcore.restore_entry("test", "durable", "test restore", "codex", "terminal"), "restore failed")
        require(memcore.search("contenu modifié", "test"), "restored entry not searchable")

        fake_secret = "sk-proj-" + "A" * 28
        try:
            memcore.add_entry("test", "reference", "blocked", fake_secret, actor="codex", origin="terminal")
            raise AssertionError("secret was accepted")
        except memcore.ValidationError as exc:
            require(str(exc).startswith("secret_detected"), "wrong secret error")
        require(memcore.get_entry("test", "blocked", True) is None, "secret row exists")

        try:
            memcore.delete_entry("test", "durable")
            raise AssertionError("physical delete was accepted")
        except memcore.ValidationError:
            pass

        events = memcore.get_events("test", "durable", 50)
        operations = {event["operation"] for event in events}
        require({"memory_create", "memory_update", "memory_archive", "memory_restore"} <= operations,
                f"missing audit events: {operations}")

        health = memcore.healthcheck()
        require(health["ok"], f"healthcheck failed: {health}")

        bridge = Path(__file__).parent / "memcore_bridge.py"
        env = dict(os.environ)
        proc = subprocess.run(
            [sys.executable, str(bridge), "--actor", "codex", "--origin", "agentroom", "--session-ref", "room:1/run:1"],
            input='{"op":"memory_search","query":"contenu","scope":"test"}\n',
            text=True, capture_output=True, env=env, timeout=15,
        )
        require(proc.returncode == 0 and '"ok": true' in proc.stdout.lower(), f"bridge failed: {proc.stderr} {proc.stdout}")

    print("PHASE_A_TESTS_OK")


if __name__ == "__main__":
    main()
