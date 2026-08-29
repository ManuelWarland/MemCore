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

        # Secrets are REDACTED (not rejected): the note is kept, the value stripped.
        fake_secret = "avant ghp_" + "A" * 36 + " apres"
        meta = memcore.add_entry("test", "reference", "redacted-note", fake_secret,
                                 actor="codex", origin="terminal", return_meta=True)
        require("github_token" in meta["redacted"], "secret not flagged as redacted")
        stored = memcore.get_entry("test", "redacted-note", True)
        require(stored is not None, "redacted note was not stored")
        require("ghp_" not in stored["content"] and "[REDACTED]" in stored["content"], "secret not stripped")
        require("avant" in stored["content"] and "apres" in stored["content"], "surrounding text lost")

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

        # Semantic search (skipped if fastembed + sqlite-vec aren't installed)
        if memcore.semantic_available():
            memcore.add_entry("sem", "reference", "a", "Le capteur de couleur tombe en panne au bout de deux cycles I2C", actor="t", origin="t")
            memcore.add_entry("sem", "reference", "b", "Sauvegarde chiffree du vault vers un stockage cloud chaque semaine", actor="t", origin="t")
            bf = memcore.embed_backfill()
            require(bf.get("ok") and bf["embedded"] >= 2, f"backfill failed: {bf}")
            st = memcore.embed_status()
            require(st["embedded"] >= 2, f"embed_status wrong: {st}")
            hits = memcore.search("comment je protege mes fichiers", scope="sem", semantic=True)
            require(hits and hits[0]["name"] == "b", f"semantic search wrong: {[h['name'] for h in hits]}")
            hyb = memcore.search("stockage cloud", scope="sem", debug=True)
            require(hyb["mode"] in ("hybrid", "and", "or_fallback"), f"unexpected hybrid mode: {hyb['mode']}")

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
