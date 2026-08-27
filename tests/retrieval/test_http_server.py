"""只读检索 HTTP：token 闸、绑定闸、JSON 契约、路径越界。"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from akb2.retrieval import engine
from akb2.retrieval.http_server import (
    MIN_TOKEN_LEN,
    RetrievalConfigError,
    build_server,
    search_payload,
    validate_bind,
    validate_token,
)

TOKEN = "t" * MIN_TOKEN_LEN
REPO = Path(__file__).resolve().parents[2]

_PROMOTED = """---
title: Loop Engineering 黄金样张
page_status: active
status: active
slug: addy-osmani-loop-engineering
gate_decision: active
reviewer: akb2-quality-loop-v1
reviewed_at: 2026-06-11
related_concepts: ["[[concept-harness-engineering]]"]
---

## 论证与解释

正文 loop engineering。见 [[loop-engineering]]。

### 操作步骤

1. 跑测试 `pytest -q tests/`
"""


def _make_vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    sources = root / "wiki" / "sources"
    sources.mkdir(parents=True)
    (sources / "addy-osmani-loop-engineering.md").write_text(_PROMOTED, encoding="utf-8")
    (sources / "not-promoted.md").write_text(
        "---\nstatus: candidate\ngate_decision: null\n---\nloop engineering draft\n",
        encoding="utf-8",
    )
    return root


def _start(vault: Path, bind: str = "127.0.0.1") -> tuple[object, str]:
    httpd = build_server(bind=bind, port=0, vault_root=vault, token=TOKEN)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    return httpd, f"http://{host}:{port}"


def _req(url: str, *, token: str | None, method: str = "GET", data: bytes | None = None) -> tuple[int, dict]:
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return resp.status, body
    except urllib.error.HTTPError as exc:
        payload = json.loads(exc.read().decode("utf-8"))
        return exc.code, payload


def test_validate_token_rejects_missing_and_short() -> None:
    with pytest.raises(RetrievalConfigError):
        validate_token(None)
    with pytest.raises(RetrievalConfigError):
        validate_token("short")
    with pytest.raises(RetrievalConfigError):
        validate_token(" " * MIN_TOKEN_LEN)
    assert validate_token(TOKEN) == TOKEN


def test_validate_bind_rejects_wildcard() -> None:
    for bad in ("0.0.0.0", "::", "[::]", "*", ""):
        with pytest.raises(RetrievalConfigError):
            validate_bind(bad)
    assert validate_bind("127.0.0.1") == "127.0.0.1"
    assert validate_bind("172.26.163.197") == "172.26.163.197"


def test_build_server_rejects_wildcard_even_with_token(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    with pytest.raises(RetrievalConfigError):
        build_server(bind="0.0.0.0", port=8787, vault_root=vault, token=TOKEN)


def test_health_does_not_need_token(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    httpd, base = _start(vault)
    try:
        status, payload = _req(f"{base}/v1/health", token=None)
        assert status == 200
        assert payload["ok"] is True
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_search_requires_token(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    httpd, base = _start(vault)
    try:
        status, payload = _req(
            f"{base}/v1/search",
            token=None,
            method="POST",
            data=json.dumps({"query": "loop"}).encode(),
        )
        assert status == 401
        assert payload == {"error": "unauthorized"}
        status, payload = _req(
            f"{base}/v1/search",
            token="wrong" + TOKEN[5:],
            method="POST",
            data=json.dumps({"query": "loop"}).encode(),
        )
        assert status == 401
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_search_post_returns_cli_contract_plus_source(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    httpd, base = _start(vault)
    try:
        status, payload = _req(
            f"{base}/v1/search",
            token=TOKEN,
            method="POST",
            data=json.dumps({"query": "loop engineering", "limit": 5}).encode(),
        )
        assert status == 200
        assert payload["query"] == "loop engineering"
        assert payload["limit"] == 5
        assert payload["count"] == 1
        assert payload["source"] == "live"
        hit = payload["results"][0]
        assert hit["path"] == "wiki/sources/addy-osmani-loop-engineering.md"
        assert "pytest -q tests/" in hit["how_executability"]
        assert hit["related_concepts"] == ["concept-harness-engineering"]
        assert "loop-engineering" in hit["graph_links"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_search_default_limit_returns_all_hits(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    sources = vault / "wiki" / "sources"
    for i in range(3):
        sources.joinpath(f"graph-note-{i}.md").write_text(
            f"""---
title: Graph note {i}
page_status: active
status: active
gate_decision: active
reviewer: akb2-quality-loop-v1
related_concepts: ["[[concept-agent-collaboration]]"]
---

graph 正文 {i} 见 [[quality-gate]]。
""",
            encoding="utf-8",
        )
    httpd, base = _start(vault)
    try:
        status, payload = _req(
            f"{base}/v1/search",
            token=TOKEN,
            method="POST",
            data=json.dumps({"query": "graph"}).encode(),
        )
        assert status == 200
        assert payload["limit"] == 0
        assert payload["count"] == 3
        assert {hit["title"] for hit in payload["results"]} == {
            "Graph note 0",
            "Graph note 1",
            "Graph note 2",
        }
        assert all("quality-gate" in hit["graph_links"] for hit in payload["results"])
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_note_get_and_path_traversal(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    secret = tmp_path / "secret.md"
    secret.write_text("do not leak", encoding="utf-8")
    httpd, base = _start(vault)
    try:
        status, payload = _req(
            f"{base}/v1/note?path=wiki/sources/addy-osmani-loop-engineering.md",
            token=TOKEN,
        )
        assert status == 200
        assert payload["source"] == "live"
        assert payload["title"] == "Loop Engineering 黄金样张"
        assert "pytest -q tests/" in payload["body"]

        status, payload = _req(
            f"{base}/v1/note?path=wiki/sources/not-promoted.md",
            token=TOKEN,
        )
        assert status == 404

        escaped = urllib.request.quote("../../" + secret.name)
        status, payload = _req(f"{base}/v1/note?path={escaped}", token=TOKEN)
        assert status == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_cli_serve_without_token_exits_2(repo_root: Path, tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src")
    env.pop("AKB2_RETRIEVAL_TOKEN", None)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "akb2.retrieval.cli",
            "serve",
            "--bind",
            "127.0.0.1",
            "--port",
            "8799",
            "--vault-root",
            str(vault),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(repo_root),
        timeout=10,
    )
    assert proc.returncode == 2
    assert "AKB2_RETRIEVAL_TOKEN" in proc.stderr
    assert TOKEN not in proc.stderr


def test_cli_serve_rejects_wildcard_bind(repo_root: Path, tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src")
    env["AKB2_RETRIEVAL_TOKEN"] = TOKEN
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "akb2.retrieval.cli",
            "serve",
            "--bind",
            "0.0.0.0",
            "--vault-root",
            str(vault),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(repo_root),
        timeout=10,
    )
    assert proc.returncode == 2
    assert "0.0.0.0" in proc.stderr


def test_wrapper_missing_token_exits_2(repo_root: Path, tmp_path: Path) -> None:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["AKB2_RETRIEVAL_ENV_FILE"] = str(tmp_path / "missing.env")
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    env.pop("AKB2_RETRIEVAL_TOKEN", None)
    proc = subprocess.run(
        ["bash", str(repo_root / "scripts" / "akb2-retrieval-http.sh")],
        capture_output=True,
        env=env,
        timeout=10,
    )
    stderr = proc.stderr.decode("utf-8", errors="replace")
    assert proc.returncode == 2
    assert "AKB2_RETRIEVAL_TOKEN" in stderr


def test_read_promoted_rejects_traversal_and_index(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    (vault / "wiki" / "sources" / "00-Index.md").write_text(
        "---\npage_status: active\nstatus: active\ngate_decision: active\n"
        "reviewer: akb2-quality-loop-v1\n---\nindex\n",
        encoding="utf-8",
    )
    doc = engine.read_promoted(vault, "wiki/sources/addy-osmani-loop-engineering.md")
    assert doc is not None
    assert doc.title == "Loop Engineering 黄金样张"
    assert engine.read_promoted(vault, "wiki/sources/00-Index.md") is None
    assert engine.read_promoted(vault, "../wiki/sources/addy-osmani-loop-engineering.md") is None
    assert engine.read_promoted(vault, "/etc/passwd") is None


def test_search_payload_skips_non_promoted(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    payload = search_payload(vault, "loop engineering", 10)
    assert payload["count"] == 1
    assert payload["source"] == "live"


def test_unused_port_helper_does_not_bind_all() -> None:
    # 确保测试自己也不走 0.0.0.0
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.close()
