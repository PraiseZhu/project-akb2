#!/usr/bin/env python3
"""e2e_noop_proof — 验收轮零写证明（sc-e2e-noop-proof）。

在双通道调用（CLI + MCP stdio）前后，各算一次真实 vault 与工程侧
可写目录（reports/、snapshots/、staging/）的指纹（相对路径 + size +
mtime_ns + 内容 sha256 的有序摘要），前后逐字相等才算过。

『只读所以没有安全面』是循环论证——不写是本轮要证明的结论：
  - 任何差异（含新增临时文件、mtime 变化）都阻断验收并打印差异清单
  - 指纹不可采样的目录（权限不足/不可读）按 infra_fail（exit 3）处理，
    绝不当『无差异』
  - 指纹快照与差异清单写仓外 --report-dir——本脚本正是要证明『不写』，
    往被采样目录写报告构成自我证伪

touch 反向变异不写在 expect 里靠自觉——由 sc-e2e-helper-contract 经
mutation_harness 机器化执行。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

EXIT_OK = 0
EXIT_DIFF = 1
EXIT_INFRA_FAIL = 3

# 被采样目录（vault 根 + 工程侧可写面）
_ENGINE_WRITABLE_DIRS = ("reports", "snapshots", "staging")


class UnsampledError(RuntimeError):
    """目录不可采样 → infra_fail（不得静默当作无差异）。"""


def fingerprint_dir(path: Path, label: str) -> dict[str, tuple[int, int, str]]:
    """有序指纹：相对路径 → (size, mtime_ns, sha256)。不可读即抛。"""
    if not path.exists():
        return {}
    try:
        entries: dict[str, tuple[int, int, str]] = {}
        for p in sorted(path.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(path).as_posix()
            stat = p.stat()
            entries[rel] = (
                stat.st_size,
                stat.st_mtime_ns,
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
        return entries
    except OSError as exc:
        raise UnsampledError(f"目录不可采样 [{label}]: {path}: {exc}") from exc


def _sampled_dirs(vault_root: Path) -> list[tuple[str, Path]]:
    dirs = [("vault", vault_root)]
    for name in _ENGINE_WRITABLE_DIRS:
        dirs.append((f"repo/{name}", REPO_ROOT / name))
    return dirs


def run_dual_channels(root: Path, query: str, limit: int) -> None:
    """触发与 sc-e2e-retrieval 同型的双通道调用（只跑，不比对结果）。"""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from e2e_dual_channel_retrieval import run_cli_channel, run_mcp_channel

    run_cli_channel(root, query, limit)
    run_mcp_channel(root, query, limit)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="e2e_noop_proof",
        description="验收轮零写证明：双通道调用前后指纹逐字相等",
    )
    parser.add_argument("--query", required=True)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--report-dir", required=True, help="仓外报告目录")
    parser.add_argument("--vault-root", default=None)
    args = parser.parse_args(argv)

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from e2e_precheck import EXIT_INFRA_FAIL, check_root  # noqa: F811

    if args.vault_root:
        root = Path(args.vault_root).expanduser().resolve()
    else:
        sys.path.insert(0, str(REPO_ROOT / "src"))
        from akb2.loops.paths import get_akb2_root

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

    report_dir = Path(args.report_dir).expanduser().resolve()
    if report_dir.is_relative_to(REPO_ROOT):
        print(f"E2E_CONFIG_FAIL 报告目录必须在仓外: {report_dir}")
        return 1

    dirs = _sampled_dirs(root)

    # 前置指纹
    try:
        before = {label: fingerprint_dir(p, label) for label, p in dirs}
    except UnsampledError as exc:
        print(f"E2E_INFRA_FAIL {exc}")
        return EXIT_INFRA_FAIL

    # 双通道调用（真实负载）
    try:
        run_dual_channels(root, args.query, args.limit)
    except Exception as exc:
        print(f"E2E_INFRA_FAIL 双通道执行失败: {exc}")
        return EXIT_INFRA_FAIL

    # 后置指纹 + 逐字比对
    try:
        after = {label: fingerprint_dir(p, label) for label, p in dirs}
    except UnsampledError as exc:
        print(f"E2E_INFRA_FAIL {exc}")
        return EXIT_INFRA_FAIL

    diffs: dict[str, dict] = {}
    for label, _ in dirs:
        b, a = before[label], after[label]
        if b == a:
            continue
        diffs[label] = {
            "added": sorted(set(a) - set(b)),
            "removed": sorted(set(b) - set(a)),
            "changed": sorted(k for k in b if k in a and b[k] != a[k]),
        }

    # 证据写仓外（不写被采样目录——自我证伪防护）
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "e2e-noop-proof-report.json").write_text(
        json.dumps(
            {
                "query": args.query,
                "sampled_dirs": [lbl for lbl, _ in dirs],
                "unchanged": not diffs,
                "diffs": diffs,
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )

    if diffs:
        for label, d in diffs.items():
            for kind in ("added", "removed", "changed"):
                for item in d[kind]:
                    print(f"E2E_NOOP_DIFF dir={label} {kind}: {item}")
        total = sum(
            len(d["added"]) + len(d["removed"]) + len(d["changed"])
            for d in diffs.values()
        )
        print(f"E2E_NOOP_FAIL 共 {total} 处差异")
        return EXIT_DIFF

    print(f"E2E_NOOP_OK dirs={len(dirs)} unchanged")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
