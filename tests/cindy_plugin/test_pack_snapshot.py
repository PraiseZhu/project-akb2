"""scripts/pack-akb2-live-snapshot.py 只读打包 promoted 笔记。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_PROMOTED = """---
title: Snapshot Probe
page_status: active
status: active
slug: snapshot-probe
gate_decision: active
reviewer: akb2-quality-loop-v1
---

## 论证与解释

snapshot probe body.

### 操作步骤

1. 跑 `echo snapshot`
"""


def test_pack_snapshot_writes_promoted_only(repo_root: Path, tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    sources = vault / "wiki" / "sources"
    sources.mkdir(parents=True)
    (sources / "snapshot-probe.md").write_text(_PROMOTED, encoding="utf-8")
    (sources / "draft.md").write_text(
        "---\nstatus: candidate\n---\nshould not pack\n",
        encoding="utf-8",
    )
    out = tmp_path / "snapshot.json"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src")
    proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "pack-akb2-live-snapshot.py"),
            "--vault-root",
            str(vault),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(repo_root),
        timeout=20,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload["notes"]) == 1
    assert payload["notes"][0]["path"] == "wiki/sources/snapshot-probe.md"
    assert payload["notes"][0]["title"] == "Snapshot Probe"


def test_ghost_manifest_has_node_secret_and_tools() -> None:
    manifest = json.loads(
        Path("cindy-plugin/akb2-live/ghost.json").read_text(encoding="utf-8")
    )
    assert manifest["id"] == "akb2-live"
    assert manifest["schemaVersion"] == 2
    assert "node" in manifest["slots"]
    assert "tool" in manifest["slots"]
    assert manifest["node"]["protocol"] == "json-rpc-stdio"
    keys = [b["key"] for b in manifest["node"]["secretBindings"]]
    assert keys == ["retrieval_token"]
    tools = [t["name"] for t in manifest["tools"]]
    assert tools == ["search", "get"]
    assert manifest["settingsHtml"] == "settings.html"
