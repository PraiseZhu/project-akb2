"""Cindy 插件 Node worker：打 live HTTP，失败时走 snapshot。"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

from akb2.retrieval.http_server import MIN_TOKEN_LEN, build_server

TOKEN = "k" * MIN_TOKEN_LEN
REPO = Path(__file__).resolve().parents[2]
WORKER = REPO / "cindy-plugin" / "akb2-live" / "node" / "worker.cjs"

_PROMOTED = """---
title: Loop Engineering 黄金样张
page_status: active
status: active
slug: addy-osmani-loop-engineering
gate_decision: active
reviewer: akb2-quality-loop-v1
---

## 论证与解释

live loop engineering 正文。

### 操作步骤

1. 跑 `pytest -q tests/`
"""


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    sources = root / "wiki" / "sources"
    sources.mkdir(parents=True)
    (sources / "addy-osmani-loop-engineering.md").write_text(_PROMOTED, encoding="utf-8")
    return root


def _rpc(proc: subprocess.Popen, method: str, params: dict, *, token: str | None) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }
    if token:
        payload["cindy"] = {"secrets": {"retrieval_token": token}}
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()
    assert proc.stdout is not None
    line = proc.stdout.readline()
    return json.loads(line)


def _spawn_worker(env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        ["node", str(WORKER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def test_worker_live_search_and_get(tmp_path: Path) -> None:
    import os

    vault = _vault(tmp_path)
    httpd = build_server(bind="127.0.0.1", port=0, vault_root=vault, token=TOKEN)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    env = os.environ.copy()
    proc = _spawn_worker(env)
    try:
        result = _rpc(
            proc,
            "search",
            {"query": "loop engineering", "limit": 5, "host": host, "port": port},
            token=TOKEN,
        )
        assert "result" in result
        body = result["result"]
        assert body["source"] == "live"
        assert body["count"] == 1
        path = body["results"][0]["path"]
        got = _rpc(proc, "get", {"path": path, "host": host, "port": port}, token=TOKEN)
        assert got["result"]["source"] == "live"
        assert "pytest -q tests/" in got["result"]["body"]
    finally:
        proc.kill()
        httpd.shutdown()
        httpd.server_close()


def test_worker_snapshot_fallback_when_live_down(tmp_path: Path) -> None:
    import os

    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "notes": [
                    {
                        "path": "wiki/sources/addy-osmani-loop-engineering.md",
                        "title": "Loop Engineering 黄金样张",
                        "body": "snapshot loop engineering 正文",
                        "how_density": "1. 跑 `pytest -q tests/`",
                        "how_executability": ["pytest -q tests/"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["AKB2_PLUGIN_SNAPSHOT"] = str(snapshot)
    proc = _spawn_worker(env)
    try:
        result = _rpc(
            proc,
            "search",
            {"query": "loop engineering", "host": "127.0.0.1", "port": 1},
            token=TOKEN,
        )
        body = result["result"]
        assert body["source"] == "snapshot"
        assert body["degraded"] is True
        assert body["count"] == 1
        assert body["results"][0]["title"] == "Loop Engineering 黄金样张"
    finally:
        proc.kill()


def test_worker_unauthorized_does_not_fall_back_to_snapshot(tmp_path: Path) -> None:
    import os

    vault = _vault(tmp_path)
    httpd = build_server(bind="127.0.0.1", port=0, vault_root=vault, token=TOKEN)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "notes": [
                    {
                        "path": "wiki/sources/addy-osmani-loop-engineering.md",
                        "title": "should not appear",
                        "body": "loop engineering",
                        "how_density": "",
                        "how_executability": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["AKB2_PLUGIN_SNAPSHOT"] = str(snapshot)
    proc = _spawn_worker(env)
    try:
        result = _rpc(
            proc,
            "search",
            {"query": "loop engineering", "host": host, "port": port},
            token="x" * MIN_TOKEN_LEN,
        )
        assert result["error"]["message"] == "unauthorized"
    finally:
        proc.kill()
        httpd.shutdown()
        httpd.server_close()
