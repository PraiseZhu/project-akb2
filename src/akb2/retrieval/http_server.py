"""AKB2 只读检索 HTTP 入口：token 保护、内网 IP 绑定、promoted 笔记检索。

只读契约与 engine 相同：不写 vault 任何字节、不调 LLM。
启动 fail-closed：缺 token / token 过短 / 绑定全接口 / vault 根不可解析 → 拒绝监听。

不是网页 UI：仅 JSON API，给同事 Cindy 插件的 Node worker 直连。
"""

from __future__ import annotations

import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from akb2.loops.paths import get_akb2_root
from akb2.retrieval import engine

# 具名常量（constitution P4：预算/门限不得用裸字面量）
MIN_TOKEN_LEN = 32
DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_LIMIT = 10
MAX_LIMIT = 50
MAX_QUERY_CHARS = 500
MAX_BODY_BYTES = 64 * 1024
FORBIDDEN_BINDS = frozenset({"0.0.0.0", "::", "[::]", "*", ""})


class RetrievalConfigError(ValueError):
    """启动配置不合法（缺 token / 绑全接口 / 坏端口）。"""


def validate_token(token: str | None) -> str:
    """token 必须存在、无空白、长度 >= MIN_TOKEN_LEN。失败信息不回显 token。"""
    if token is None or token == "":
        raise RetrievalConfigError(
            f"AKB2_RETRIEVAL_TOKEN 未设置（fail-closed，最短 {MIN_TOKEN_LEN} 字符）"
        )
    if any(ch.isspace() for ch in token):
        raise RetrievalConfigError("AKB2_RETRIEVAL_TOKEN 含空白（fail-closed）")
    if len(token) < MIN_TOKEN_LEN:
        raise RetrievalConfigError(
            f"AKB2_RETRIEVAL_TOKEN 短于 {MIN_TOKEN_LEN} 字符（fail-closed）"
        )
    return token


def validate_bind(bind: str | None) -> str:
    """拒绝全接口绑定。必须是 127.0.0.1 或明确的内网单播地址。"""
    raw = (bind or "").strip()
    if raw.lower() in FORBIDDEN_BINDS or raw in FORBIDDEN_BINDS:
        raise RetrievalConfigError(
            "拒绝绑定 0.0.0.0/::（fail-closed）。请设 AKB2_RETRIEVAL_BIND 为内网 IP 或 127.0.0.1"
        )
    return raw


def validate_port(port: int) -> int:
    if not isinstance(port, int) or port < 1 or port > 65535:
        raise RetrievalConfigError(f"端口不合法: {port}（期望 1–65535）")
    return port


def _token_matches(provided: str, expected: str) -> bool:
    """恒定时间比较；长度不同时仍做一次 compare_digest，避免抛 ValueError。"""
    a = provided.encode("utf-8")
    b = expected.encode("utf-8")
    if len(a) != len(b):
        hmac.compare_digest(b, b)
        return False
    return hmac.compare_digest(a, b)


def search_payload(root: Path, query: str, limit: int) -> dict[str, Any]:
    """与 CLI JSON 契约同四键，另加 source=live。"""
    capped = DEFAULT_LIMIT if limit <= 0 else min(limit, MAX_LIMIT)
    report = engine.scan_report(root, query, limit=capped)
    return {
        "query": query,
        "limit": capped,
        "count": len(report.results),
        "results": [r.to_dict() for r in report.results],
        "source": "live",
        "skipped": len(report.skipped),
    }


class RetrievalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    vault_root: Path
    token: str

    def __init__(
        self,
        server_address: tuple[str, int],
        vault_root: Path,
        token: str,
    ) -> None:
        self.vault_root = vault_root
        self.token = token
        super().__init__(server_address, RetrievalHandler)


class RetrievalHandler(BaseHTTPRequestHandler):
    server_version = "akb2-retrieval/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        # 访问日志走 stderr；不打印 Authorization / token。
        sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _unauthorized(self) -> None:
        self._send_json(401, {"error": "unauthorized"})

    def _require_token(self) -> bool:
        raw = self.headers.get("Authorization", "")
        provided = raw[7:] if raw.startswith("Bearer ") else ""
        expected = self.server.token
        if not isinstance(expected, str) or not _token_matches(provided, expected):
            self._unauthorized()
            return False
        return True

    def _read_json_body(self) -> dict[str, Any] | None:
        length_raw = self.headers.get("Content-Length", "0")
        try:
            length = int(length_raw)
        except ValueError:
            self._send_json(400, {"error": "bad_content_length"})
            return None
        if length < 0 or length > MAX_BODY_BYTES:
            self._send_json(413, {"error": "body_too_large"})
            return None
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid_json"})
            return None
        if not isinstance(data, dict):
            self._send_json(400, {"error": "json_object_required"})
            return None
        return data

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler 约定
        parsed = urlparse(self.path)
        if parsed.path == "/v1/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "bind": self.server.server_address[0],
                    "port": self.server.server_address[1],
                    "vault_ok": True,
                },
            )
            return
        if parsed.path == "/v1/search":
            if not self._require_token():
                return
            qs = parse_qs(parsed.query)
            query = (qs.get("query") or qs.get("q") or [""])[0]
            try:
                limit = int((qs.get("limit") or [str(DEFAULT_LIMIT)])[0])
            except ValueError:
                self._send_json(400, {"error": "invalid_limit"})
                return
            self._handle_search(query, limit)
            return
        if parsed.path == "/v1/note":
            if not self._require_token():
                return
            qs = parse_qs(parsed.query)
            rel = (qs.get("path") or [""])[0]
            self._handle_note(rel)
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/v1/search":
            self._send_json(404, {"error": "not_found"})
            return
        if not self._require_token():
            return
        data = self._read_json_body()
        if data is None:
            return
        query = data.get("query", "")
        if not isinstance(query, str):
            self._send_json(400, {"error": "query_must_be_string"})
            return
        limit_raw = data.get("limit", DEFAULT_LIMIT)
        if isinstance(limit_raw, bool) or not isinstance(limit_raw, int):
            self._send_json(400, {"error": "invalid_limit"})
            return
        self._handle_search(query, limit_raw)

    def do_PUT(self) -> None:  # noqa: N802
        self._send_json(405, {"error": "method_not_allowed"})

    def do_DELETE(self) -> None:  # noqa: N802
        self._send_json(405, {"error": "method_not_allowed"})

    def _handle_search(self, query: str, limit: int) -> None:
        if len(query) > MAX_QUERY_CHARS:
            self._send_json(413, {"error": "query_too_long"})
            return
        root = self.server.vault_root
        self._send_json(200, search_payload(root, query, limit))

    def _handle_note(self, rel_path: str) -> None:
        root = self.server.vault_root
        doc = engine.read_promoted(root, rel_path)
        if doc is None:
            self._send_json(404, {"error": "not_found"})
            return
        self._send_json(200, {"source": "live", **doc.to_dict()})


def build_server(
    *,
    bind: str,
    port: int,
    vault_root: Path,
    token: str,
) -> RetrievalHTTPServer:
    """构造已绑定的 server（port=0 仅供测试拿临时端口）。"""
    token = validate_token(token)
    bind = validate_bind(bind)
    if port != 0:
        validate_port(port)
    httpd = RetrievalHTTPServer((bind, port), vault_root=vault_root, token=token)
    return httpd


def run_server(
    *,
    bind: str | None,
    port: int | None,
    vault_root: Path | None,
    token: str | None,
) -> int:
    """阻塞监听。配置失败返回 2；vault 解析失败返回 3。"""
    try:
        resolved_token = validate_token(token)
        resolved_bind = validate_bind(bind or os.environ.get("AKB2_RETRIEVAL_BIND") or DEFAULT_BIND)
        resolved_port = validate_port(
            port if port is not None else int(os.environ.get("AKB2_RETRIEVAL_PORT") or DEFAULT_PORT)
        )
    except (RetrievalConfigError, ValueError) as exc:
        print(f"检索 HTTP 启动失败: {exc}", file=sys.stderr)
        return 2
    try:
        root = vault_root if vault_root is not None else get_akb2_root()
    except RuntimeError as exc:
        print(f"vault 根解析失败: {exc}", file=sys.stderr)
        return 3
    httpd = build_server(
        bind=resolved_bind,
        port=resolved_port,
        vault_root=root,
        token=resolved_token,
    )
    host, actual_port = httpd.server_address
    print(
        f"akb2-retrieval-http listening on {host}:{actual_port} (vault_ok=1)",
        file=sys.stderr,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("akb2-retrieval-http interrupted", file=sys.stderr)
    finally:
        httpd.server_close()
    return 0
