"""AKB2 检索 CLI：python3 -m akb2.retrieval.cli search <query> [--limit N] [--output json]。

vault 根经 akb2.loops.paths.get_akb2_root() 解析（env VAULT_ROOT/AKB2_ROOT
优先，config/vault.toml 兜底），不硬编码；全程只读。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from akb2.loops.paths import get_akb2_root
from akb2.retrieval import engine


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m akb2.retrieval.cli",
        description="AKB2 promoted 笔记只读检索（主链路已升格产出）",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    search_p = sub.add_parser("search", help="关键词检索 promoted 笔记")
    search_p.add_argument("query", help="检索词（空白分词，AND 匹配）")
    search_p.add_argument("--limit", type=int, default=10, help="返回条数上限")
    search_p.add_argument(
        "--output",
        choices=["json"],
        default="json",
        help="输出格式（当前仅 json）",
    )
    search_p.add_argument(
        "--vault-root",
        type=Path,
        default=None,
        help="显式指定 vault 根（默认经 config/vault.toml 解析）",
    )
    serve_p = sub.add_parser(
        "serve",
        help="启动只读检索 HTTP（token 保护；给 Cindy 插件直连，不是网页）",
    )
    serve_p.add_argument(
        "--bind",
        default=None,
        help="绑定地址（默认 env AKB2_RETRIEVAL_BIND 或 127.0.0.1；拒绝 0.0.0.0）",
    )
    serve_p.add_argument(
        "--port",
        type=int,
        default=None,
        help="端口（默认 env AKB2_RETRIEVAL_PORT 或 8787）",
    )
    serve_p.add_argument(
        "--vault-root",
        type=Path,
        default=None,
        help="显式指定 vault 根（默认经 config/vault.toml 解析）",
    )
    return parser


def _run_search(args: argparse.Namespace) -> int:
    try:
        root = args.vault_root if args.vault_root is not None else get_akb2_root()
    except RuntimeError as exc:
        # fail-closed（sc-engine-failclosed）：解析失败给足诊断（期望值 vs
        # 实际值），诊断模板不含密钥/PII——哨兵断言由测试承载。
        print(f"vault 根解析失败: {exc}", file=sys.stderr)
        return 3
    report = engine.scan_report(root, args.query, limit=args.limit)
    # 单条不可解析跳过 → stderr 可见计数（部分失败显式化，constitution P4）；
    # stdout 的 JSON 契约四键不变（readme 契约钉死）。
    for path, reason in report.skipped:
        print(f"skipped: {path} ({reason})", file=sys.stderr)
    payload = {
        "query": args.query,
        "limit": args.limit,
        "count": len(report.results),
        "results": [r.to_dict() for r in report.results],
    }
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


def _run_serve(args: argparse.Namespace) -> int:
    from akb2.retrieval.http_server import run_server

    # token 只从 env 读，不接受 CLI 参数（会出现在 ps 命令行）。
    return run_server(
        bind=args.bind,
        port=args.port,
        vault_root=args.vault_root,
        token=os.environ.get("AKB2_RETRIEVAL_TOKEN"),
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "search":
        return _run_search(args)
    if args.command == "serve":
        return _run_serve(args)
    print(f"未知命令: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
