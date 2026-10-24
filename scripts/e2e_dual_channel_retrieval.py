#!/usr/bin/env python3
"""e2e_dual_channel_retrieval — 双通道端到端检索验收（sc-e2e-retrieval）。

同一真实任务型查询分别经：
  (a) 引擎 CLI（python3 -m akb2.retrieval.cli search）
  (b) 注册后的 MCP server（stdio client 调 akb2_retrieval_search）
各调一次，两通道结果规范化后（笔记相对路径 + 标题 + how_density +
how_executability）逐字相等才算过。

判据（不用引擎自定义近似）：
  - 结果 ≥1 条
  - 每条命中权威 promoted 判据（status/page_status: active +
    gate_decision: active + reviewer: akb2-quality-loop-v1 + 落点
    wiki/sources/——与旧主链路 promote.py _flip_frontmatter 同口径；promote.py 已于
    2026-10-24 随主链路删除，reviewer 值冻结为删除时的常量，不取引擎自己的常量自证）
  - How 字段非空且类型正确（how_density: str / how_executability: list）

只读：不写 vault、不写工程数据（零写由 e2e_noop_proof 实测承载）。
验收证据写仓外 --report-dir（仓内写入会让 wrapper dirty=0 误红）。

输出：E2E_OK cli=<N> mcp=<N> identical=true（成功）；
不等 → 非 0 并打印首个差异字段与差异条目。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = REPO_ROOT / "toolbox" / "cc-akb2-retrieval" / "mcp_server" / "server.py"

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_INFRA_FAIL = 3


# 删除前 src/akb2/quality/promote.py 的 MACHINE_REVIEWER；现存已升格笔记都带这个签名
PROMOTED_REVIEWER = "akb2-quality-loop-v1"


def _machine_reviewer() -> str:
    return PROMOTED_REVIEWER


def _normalize(results: list[dict]) -> list[dict]:
    """规范化：只留契约四字段（score 属通道实现细节，不参与一致性比对）。"""
    out = []
    for r in results:
        out.append(
            {
                "path": r["path"],
                "title": r["title"],
                "how_density": r["how_density"],
                "how_executability": list(r["how_executability"]),
            }
        )
    return out


def run_cli_channel(root: Path, query: str, limit: int) -> list[dict]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    proc = subprocess.run(
        [
            sys.executable, "-m", "akb2.retrieval.cli",
            "search", query, "--limit", str(limit),
            "--vault-root", str(root),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"CLI 通道失败 rc={proc.returncode}: {proc.stderr[:300]}")
    return json.loads(proc.stdout)["results"]


def run_mcp_channel(root: Path, query: str, limit: int) -> list[dict]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = os.environ.copy()
    env["AKB2_ROOT"] = str(root)
    env.pop("VAULT_ROOT", None)
    env.pop("AKB2_VAULT_ROOT", None)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    params = StdioServerParameters(
        command=sys.executable, args=[str(SERVER_PATH)], env=env
    )

    async def _drive() -> str:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "akb2_retrieval_search", {"query": query, "limit": limit}
                )
                for item in result.content or []:
                    text = getattr(item, "text", None)
                    if text is not None:
                        return text
                raise RuntimeError("MCP 工具结果无 text content")

    return json.loads(asyncio.run(_drive()))["results"]


def verify_promoted_authority(root: Path, results: list[dict]) -> None:
    """逐条按权威 promoted 判据复核（引擎自述不可信，二次独立验证）。"""
    reviewer = _machine_reviewer()
    status_re = re.compile(r"^(page_)?status:\s*active\s*$", re.M)
    gate_re = re.compile(r"^gate_decision:\s*active\s*$", re.M)
    reviewer_re = re.compile(rf"^reviewer:\s*{re.escape(reviewer)}\s*$", re.M)
    fm_re = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

    for r in results:
        rel = r["path"]
        if not rel.startswith("wiki/sources/"):
            raise MismatchError(f"{rel}: 落点不在 wiki/sources/（非 promoted 唯一落点）")
        note = (root / rel).read_text(encoding="utf-8")
        m = fm_re.match(note)
        if not m:
            raise MismatchError(f"{rel}: 无 frontmatter，不可能 promoted")
        fm = m.group(1)
        if not (status_re.search(fm) and gate_re.search(fm) and reviewer_re.search(fm)):
            raise MismatchError(f"{rel}: frontmatter 不满足权威 promoted 四要素")
        if not isinstance(r["how_density"], str):
            raise MismatchError(f"{rel}: how_density 类型非 str")
        if not r["how_density"].strip():
            raise MismatchError(f"{rel}: how_density 为空（promoted 笔记缺 How 节）")
        if not isinstance(r["how_executability"], list) or not all(
            isinstance(c, str) for c in r["how_executability"]
        ):
            raise MismatchError(f"{rel}: how_executability 类型非 list[str]")


class MismatchError(RuntimeError):
    """双通道不一致 / 判据不符 → exit 1。"""


def describe_first_mismatch(
    cli_results: list[dict], mcp_results: list[dict]
) -> str | None:
    """比对两通道原始结果，返回首个差异描述（None = 逐字相等）。

    独立纯函数：黑盒契约测试（sc-e2e-helper-contract b 格）直接构造
    不一致夹具喂本函数，防『伪造 identical=true』的比对逻辑空转。
    """
    if not cli_results:
        return "CLI 通道零结果（真实 vault 上任务型查询应 ≥1）"
    cli_norm = _normalize(cli_results)
    mcp_norm = _normalize(mcp_results)
    if cli_norm == mcp_norm:
        return None
    for i, (c, m) in enumerate(zip(cli_norm, mcp_norm, strict=False)):
        if c != m:
            for field in c:
                if c[field] != m.get(field):
                    return (
                        f"首个差异: 条目#{i} 字段={field} "
                        f"cli={json.dumps(c[field], ensure_ascii=False)[:120]} "
                        f"mcp={json.dumps(m.get(field), ensure_ascii=False)[:120]}"
                    )
            return f"条目#{i} 整条不等"
    return f"条数不等 cli={len(cli_norm)} mcp={len(mcp_norm)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="e2e_dual_channel_retrieval",
        description="双通道（CLI + MCP stdio）检索一致性端到端验收",
    )
    parser.add_argument("--query", required=True, help="真实任务型查询词")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument(
        "--report-dir", required=True, help="仓外报告目录（不得位于仓内）"
    )
    parser.add_argument("--vault-root", default=None, help="显式 vault 根（默认经 config 解析）")
    args = parser.parse_args(argv)

    # 前置：环境闸（未就绪 → exit 3，不吞）
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from e2e_precheck import EXIT_INFRA_FAIL, check_root  # noqa: E402

    if args.vault_root:
        root = Path(args.vault_root).expanduser().resolve()
    else:
        sys.path.insert(0, str(REPO_ROOT / "src"))
        from akb2.loops.paths import get_akb2_root  # noqa: E402

        try:
            root = get_akb2_root()
        except RuntimeError as exc:
            print(f"E2E_INFRA_FAIL vault 根解析失败: {exc}")
            return EXIT_INFRA_FAIL
    try:
        check_root(root)
    except Exception as exc:
        print(f"E2E_INFRA_FAIL {exc}")
        return EXIT_INFRA_FAIL

    try:
        cli_results = run_cli_channel(root, args.query, args.limit)
        mcp_results = run_mcp_channel(root, args.query, args.limit)
    except Exception as exc:
        print(f"E2E_INFRA_FAIL 通道执行失败: {exc}")
        return EXIT_INFRA_FAIL

    try:
        verify_promoted_authority(root, cli_results)
        mismatch = describe_first_mismatch(cli_results, mcp_results)
        if mismatch is not None:
            print(f"E2E_MISMATCH {mismatch}")
            return EXIT_MISMATCH
        cli_norm = _normalize(cli_results)
        mcp_norm = _normalize(mcp_results)
    except MismatchError as exc:
        print(f"E2E_MISMATCH {exc}")
        return EXIT_MISMATCH

    # 验收证据写仓外
    report_dir = Path(args.report_dir).expanduser().resolve()
    if report_dir.is_relative_to(REPO_ROOT):
        print(f"E2E_CONFIG_FAIL 报告目录必须在仓外: {report_dir}")
        return 1
    report_dir.mkdir(parents=True, exist_ok=True)
    digest_src = json.dumps(cli_norm, ensure_ascii=False, sort_keys=True)
    import hashlib

    report = {
        "query": args.query,
        "limit": args.limit,
        "root": str(root),
        "cli_count": len(cli_norm),
        "mcp_count": len(mcp_norm),
        "identical": True,
        "results_digest": hashlib.sha256(digest_src.encode()).hexdigest(),
        "hits": [
            {"path": r["path"], "title": r["title"]} for r in cli_norm
        ],
    }
    (report_dir / "e2e-dual-channel-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )

    print(f"E2E_OK cli={len(cli_norm)} mcp={len(mcp_norm)} identical=true")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
