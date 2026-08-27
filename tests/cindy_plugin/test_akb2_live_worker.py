"""Cindy 插件 Node worker：打 live HTTP，失败时走 snapshot。"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


def _write_snapshot(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "notes": [
                    {
                        "path": "wiki/sources/addy-osmani-loop-engineering.md",
                        "title": "should not appear from snapshot",
                        "body": "snapshot loop engineering 正文",
                        "how_density": "1. 跑 `echo snapshot`",
                        "how_executability": ["echo snapshot"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def _stub_status_server(status: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self._reply()

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length:
                self.rfile.read(length)
            self._reply()

        def _reply(self) -> None:
            body = b'{"error":"stub"}'
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def test_worker_live_search_and_get(tmp_path: Path) -> None:
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
        assert body["limit"] == 5
        path = body["results"][0]["path"]
        got = _rpc(proc, "get", {"path": path, "host": host, "port": port}, token=TOKEN)
        assert got["result"]["source"] == "live"
        assert "pytest -q tests/" in got["result"]["body"]
    finally:
        proc.kill()
        httpd.shutdown()
        httpd.server_close()


def test_worker_omitted_limit_returns_all_live_hits(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    sources = vault / "wiki" / "sources"
    for i in range(3):
        sources.joinpath(f"graph-{i}.md").write_text(
            f"""---
title: Graph {i}
page_status: active
status: active
gate_decision: active
reviewer: akb2-quality-loop-v1
related_concepts: ["[[concept-harness-engineering]]"]
---

graph 笔记 {i} [[quality-gate]]
""",
            encoding="utf-8",
        )
    httpd = build_server(bind="127.0.0.1", port=0, vault_root=vault, token=TOKEN)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    proc = _spawn_worker(os.environ.copy())
    try:
        result = _rpc(
            proc,
            "search",
            {"query": "graph", "host": host, "port": port},
            token=TOKEN,
        )
        body = result["result"]
        assert body["source"] == "live"
        assert body["limit"] == 0
        assert body["count"] == 3
        assert all("quality-gate" in hit["graph_links"] for hit in body["results"])
        assert all(
            "concept-harness-engineering" in hit["related_concepts"]
            for hit in body["results"]
        )
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


def test_worker_live_404_does_not_use_snapshot(tmp_path: Path) -> None:
    """live 已达且 get=404：笔记已不在 promoted 集，不得用过期快照顶上。"""
    vault = tmp_path / "empty-vault"
    (vault / "wiki" / "sources").mkdir(parents=True)
    snapshot = tmp_path / "snapshot.json"
    _write_snapshot(snapshot)
    httpd = build_server(bind="127.0.0.1", port=0, vault_root=vault, token=TOKEN)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    env = os.environ.copy()
    env["AKB2_PLUGIN_SNAPSHOT"] = str(snapshot)
    proc = _spawn_worker(env)
    try:
        result = _rpc(
            proc,
            "get",
            {
                "path": "wiki/sources/addy-osmani-loop-engineering.md",
                "host": host,
                "port": port,
            },
            token=TOKEN,
        )
        assert result["error"]["message"] == "not_found"
        assert "result" not in result or result.get("result") is None
    finally:
        proc.kill()
        httpd.shutdown()
        httpd.server_close()


def test_worker_live_http_error_does_not_use_snapshot(tmp_path: Path) -> None:
    """live 可达但 5xx：不是连不上，不得标 live_unreachable 后读快照。"""
    snapshot = tmp_path / "snapshot.json"
    _write_snapshot(snapshot)
    httpd = _stub_status_server(500)
    host, port = httpd.server_address
    env = os.environ.copy()
    env["AKB2_PLUGIN_SNAPSHOT"] = str(snapshot)
    proc = _spawn_worker(env)
    try:
        result = _rpc(
            proc,
            "search",
            {"query": "loop engineering", "host": host, "port": port},
            token=TOKEN,
        )
        assert result["error"]["message"] == "live_http_500"
        assert "result" not in result
        got = _rpc(
            proc,
            "get",
            {
                "path": "wiki/sources/addy-osmani-loop-engineering.md",
                "host": host,
                "port": port,
            },
            token=TOKEN,
        )
        assert got["error"]["message"] == "live_http_500"
    finally:
        proc.kill()
        httpd.shutdown()
        httpd.server_close()
