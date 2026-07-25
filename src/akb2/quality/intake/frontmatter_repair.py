"""frontmatter_repair.py — F53 存量坏 raw frontmatter 原地修复 CLI(手动,有界,fail-visible)。

背景:F52 前的 raw_archiver 手写 YAML 序列化会产出不可解析 frontmatter
(@ 开头作者/多行作者等,存量扫描 432/4184 篇)。F52 已修采集端与读取端,
本 CLI 把存量坏文件的 frontmatter 块用修复后的序列化器原地重写:
    1. 扫 raw/ 下所有 .md,frontmatter 过 yaml.safe_load,可解析 → 跳过
    2. 不可解析 → 降级逐行提取(与 distill._extract_raw_frontmatter_lines 同源)
    3. 9 字段全部恢复非空 → 用 raw_archiver._format_frontmatter_yaml 重写块
       (正文一个字节不动);任一字段缺失 → 列为 unrepairable,不动文件
    4. 全程审计 JSONL(原 frontmatter 块 + 新块 + 状态),fail-visible

宪法对齐:
    - manual 触发(不注册任何自动化触发器)
    - 有界(单纯存量扫描,无 LLM/无网络/无循环)
    - 默认 dry-run,--apply 才写文件
    - 不写 quality-ledger(raw 层修复不影响 runner 状态机;审计走独立文件)

用法:
    python3 -m akb2.quality.intake.frontmatter_repair \\
        [--vault-root "/Volumes/AKB2/Obsidian/Praise Space/AKB2.0"] \\
        [--apply] [--limit N] [--audit-out reports/frontmatter-repair.jsonl]

vault-root 默认取 AKB2_VAULT_ROOT 环境变量;二者皆缺 → fail-closed 退出。
退出码:0 = 无坏文件或全部修复;1 = 存在 unrepairable;2 = 参数/路径错误。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from src.akb2.quality.distill import _extract_raw_frontmatter_lines
from src.akb2.quality.intake.raw_archiver import (
    FRONTMATTER_FIELDS,
    _format_frontmatter_yaml,
)

_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def scan_and_repair(
    raw_dir: Path,
    apply: bool = False,
    limit: int = 0,
) -> dict[str, Any]:
    """扫描 raw_dir,修复不可解析 frontmatter。

    Args:
        raw_dir: raw 层目录。
        apply: True 才写文件;False 仅报告(dry-run)。
        limit: >0 时最多修复 N 篇(有界演练用);0 = 不限。

    Returns:
        {"scanned": int, "healthy": int, "repaired": [...], "unrepairable": [...],
         "records": [审计记录 dict]}
    """
    scanned = 0
    healthy = 0
    repaired: list[str] = []
    unrepairable: list[dict[str, str]] = []
    records: list[dict[str, Any]] = []

    for path in sorted(raw_dir.glob("*.md")):
        scanned += 1
        text = path.read_text(encoding="utf-8")
        m = _FM_RE.match(text)
        if not m:
            # 无 frontmatter 块的文件不属于本工具职责(archiver 必写块)
            healthy += 1
            continue
        fm_text = m.group(1)
        try:
            data = yaml.safe_load(fm_text)
            if isinstance(data, dict):
                healthy += 1
                continue
        except Exception:
            pass  # 不可解析 → 进修复流程

        if limit and len(repaired) >= limit:
            break

        extracted = _extract_raw_frontmatter_lines(fm_text)
        extracted.pop("_frontmatter_degraded", None)
        missing = [
            k for k in FRONTMATTER_FIELDS if not str(extracted.get(k, "")).strip()
        ]
        if missing:
            unrepairable.append({"path": str(path), "missing": ",".join(missing)})
            records.append({
                "path": str(path), "status": "unrepairable",
                "missing": missing, "old_frontmatter": fm_text,
            })
            continue

        new_block = _format_frontmatter_yaml(
            {k: str(extracted[k]) for k in FRONTMATTER_FIELDS}
        )
        # 重写块后必须可解析(自校验,防修复器自己产坏块)
        inner = new_block.strip().removeprefix("---").removesuffix("---")
        reparsed = yaml.safe_load(inner)
        assert isinstance(reparsed, dict) and reparsed.get("source_url"), (
            f"修复器自产块不可解析或缺 source_url: {path}"
        )

        new_text = new_block + "\n" + text[m.end():]
        if apply:
            path.write_text(new_text, encoding="utf-8")
        repaired.append(str(path))
        records.append({
            "path": str(path), "status": "repaired" if apply else "would_repair",
            "old_frontmatter": fm_text, "new_frontmatter": new_block,
        })

    return {
        "scanned": scanned,
        "healthy": healthy,
        "repaired": repaired,
        "unrepairable": unrepairable,
        "records": records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="F53 存量坏 raw frontmatter 原地修复(默认 dry-run)"
    )
    parser.add_argument("--vault-root", default=os.environ.get("AKB2_VAULT_ROOT", ""))
    parser.add_argument("--apply", action="store_true", help="真正写文件(默认 dry-run)")
    parser.add_argument("--limit", type=int, default=0, help=">0 时最多修 N 篇")
    parser.add_argument("--audit-out", default="", help="审计 JSONL 输出路径")
    args = parser.parse_args(argv)

    if not args.vault_root:
        print("❌ 缺 --vault-root 且 AKB2_VAULT_ROOT 未设(路径契约:不猜路径)", file=sys.stderr)
        return 2
    raw_dir = Path(args.vault_root) / "raw"
    if not raw_dir.is_dir():
        print(f"❌ raw 目录不存在: {raw_dir}", file=sys.stderr)
        return 2

    result = scan_and_repair(raw_dir, apply=args.apply, limit=args.limit)

    if args.audit_out:
        audit_path = Path(args.audit_out)
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_path, "a", encoding="utf-8") as fh:
            header = {
                "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "mode": "apply" if args.apply else "dry-run",
                "scanned": result["scanned"],
                "healthy": result["healthy"],
                "repaired_count": len(result["repaired"]),
                "unrepairable_count": len(result["unrepairable"]),
            }
            fh.write(json.dumps(header, ensure_ascii=False) + "\n")
            for rec in result["records"]:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] 扫描 {result['scanned']} 篇: "
          f"健康 {result['healthy']}, "
          f"{'已修复' if args.apply else '待修复'} {len(result['repaired'])}, "
          f"不可修复 {len(result['unrepairable'])}")
    for item in result["unrepairable"]:
        print(f"  [UNREPAIRABLE] {item['path']} 缺: {item['missing']}")
    return 1 if result["unrepairable"] else 0


if __name__ == "__main__":
    sys.exit(main())
