#!/usr/bin/env python3
"""check_pr_body_counts.py — PR body 复核轨迹表计数的机器 owner（SC-R7-05）。

PR body 是全链唯一没有机器闸的产物：复核轨迹表每行的「N 条 finding」「N 条 SC」都是手写，
从未接过任何机器来源，R7 复审抓到的漂移（R4 delta 行写「5 条 SC」，实际其绑定的
sc-manifest 只有 4 条）只是这条规律的又一次表现。本脚本不改数字，而是把每行计数与其
绑定的机器 artifact 逐一对账，杜绝下一次手抄漂移再次悄悄通过。

绑定关系（均由 artifact 自身字段声明，不靠行号猜测——R4/R5 两轮的文件编号本身就有一位
偏移，行号猜测正是本脚本要替掉的失真来源）：
  ① 行的 candidate 列（如 `92185ad`）是某个 consensus-r<N>.json 的 `candidate_sha` 前缀；
  ② 该 consensus 的 `canonical_findings` 长度 == 行「N 条」finding；
  ③ 某个 sc-manifest*.json 的 `consensus_artifact_hash` == 该 consensus 的
     `consensus_artifact_hash`（即该 SC 集合是为该轮共识的 finding 而修的）；
  ④ 该 sc-manifest 的 `scs` 长度 == 行「N 条 SC」。

任一环节失配（candidate 未绑定 / 绑定歧义 / 计数不符）均非零退出并指名行号与两侧数值。

用法：
    python3 scripts/check_pr_body_counts.py --body _tmp/pr-review/pr-body-r6.md \
        --artifacts-dir _tmp/pr-review

退出码：0 = 全部对账一致；1 = 存在计数/绑定违规；2 = body 或 artifacts-dir 不存在。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_RE_INT = re.compile(r"(\d+)")
_RE_SC_COUNT = re.compile(r"(\d+)\s*条\s*SC")

# 复核轨迹表列序（0-based）：轮次 | candidate | 共识确认 | 其中 actionable | 修复编排
_COL_LABEL, _COL_CANDIDATE, _COL_FINDINGS, _COL_ACTIONABLE, _COL_FIX = range(5)


def _split_row(line: str) -> list[str]:
    parts = [p.strip() for p in line.strip().split("|")]
    if parts and parts[0] == "":
        parts = parts[1:]
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


def _is_header_row(cols: list[str]) -> bool:
    joined = "".join(cols)
    return "轮次" in joined and "candidate" in joined and "共识确认" in joined


def _is_separator_row(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return False
    body = stripped.strip("|")
    return bool(body) and all(ch in "-:| \t" for ch in body)


class ParsedRow:
    def __init__(self, lineno: int, cols: list[str]):
        self.lineno = lineno
        self.cols = cols
        self.parse_error: str | None = None
        self.label = cols[_COL_LABEL] if len(cols) > _COL_LABEL else ""
        self.candidate = ""
        self.finding_count: int | None = None
        self.sc_count: int | None = None
        self._parse()

    def _parse(self) -> None:
        if len(self.cols) < 5:
            self.parse_error = f"列数不足 5（实际 {len(self.cols)}）"
            return
        candidate = self.cols[_COL_CANDIDATE].strip().strip("`").strip()
        if not candidate:
            self.parse_error = "candidate 列为空"
            return
        self.candidate = candidate

        finding_match = _RE_INT.search(self.cols[_COL_FINDINGS])
        if not finding_match:
            self.parse_error = f"共识确认列无法解析出数字: {self.cols[_COL_FINDINGS]!r}"
            return
        self.finding_count = int(finding_match.group(1))

        sc_match = _RE_SC_COUNT.search(self.cols[_COL_FIX])
        if not sc_match:
            self.parse_error = f"修复编排列无法解析出「N 条 SC」: {self.cols[_COL_FIX]!r}"
            return
        self.sc_count = int(sc_match.group(1))


def find_review_trail_rows(lines: list[str]) -> list[ParsedRow]:
    """定位并解析「复核轨迹」表的数据行（表头/分隔行不计入）。"""
    rows: list[ParsedRow] = []
    header_seen = False
    in_table = False
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_table:
                break
            continue
        cols = _split_row(stripped)
        if not header_seen:
            if _is_header_row(cols):
                header_seen = True
                in_table = True
            continue
        if _is_separator_row(stripped):
            continue
        rows.append(ParsedRow(lineno, cols))
    return rows


def _load_json_records(paths: list[Path]) -> list[dict]:
    records = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            records.append({"path": path, "error": str(exc)})
            continue
        records.append({"path": path, "data": data})
    return records


def load_consensus_records(artifacts_dir: Path) -> list[dict]:
    records = []
    for rec in _load_json_records(sorted(artifacts_dir.glob("consensus-r*.json"))):
        if "error" in rec:
            records.append(rec)
            continue
        data = rec["data"]
        records.append(
            {
                "path": rec["path"],
                "candidate_sha": str(data.get("candidate_sha") or ""),
                "consensus_hash": str(data.get("consensus_artifact_hash") or ""),
                "n_findings": len(data.get("canonical_findings") or []),
            }
        )
    return records


def load_sc_manifest_records(artifacts_dir: Path) -> list[dict]:
    records = []
    for rec in _load_json_records(sorted(artifacts_dir.glob("sc-manifest*.json"))):
        if "error" in rec:
            records.append(rec)
            continue
        data = rec["data"]
        records.append(
            {
                "path": rec["path"],
                "consensus_hash": str(data.get("consensus_artifact_hash") or ""),
                "n_scs": len(data.get("scs") or []),
            }
        )
    return records


def check(body_path: Path, artifacts_dir: Path) -> list[str]:
    violations: list[str] = []
    lines = body_path.read_text(encoding="utf-8").splitlines()
    rows = find_review_trail_rows(lines)
    if not rows:
        return ["未在 body 中找到复核轨迹表（表头须含「轮次」「candidate」「共识确认」三列）"]

    consensus_records = load_consensus_records(artifacts_dir)
    sc_records = load_sc_manifest_records(artifacts_dir)

    for rec in consensus_records:
        if "error" in rec:
            violations.append(f"consensus artifact 解析失败: {rec['path']}: {rec['error']}")
    for rec in sc_records:
        if "error" in rec:
            violations.append(f"sc-manifest artifact 解析失败: {rec['path']}: {rec['error']}")
    consensus_records = [r for r in consensus_records if "error" not in r]
    sc_records = [r for r in sc_records if "error" not in r]

    for row in rows:
        if row.parse_error:
            violations.append(f"line {row.lineno}: 「{row.label}」行解析失败 —— {row.parse_error}")
            continue

        matches = [
            c
            for c in consensus_records
            if c["candidate_sha"] and c["candidate_sha"].lower().startswith(row.candidate.lower())
        ]
        if not matches:
            violations.append(
                f"line {row.lineno}: 「{row.label}」行 candidate `{row.candidate}` 未匹配任何 "
                f"consensus artifact 的 candidate_sha（{artifacts_dir}）"
            )
            continue
        if len(matches) > 1:
            violations.append(
                f"line {row.lineno}: 「{row.label}」行 candidate `{row.candidate}` 匹配到 "
                f"{len(matches)} 个 consensus artifact，绑定歧义: "
                f"{[str(m['path'].name) for m in matches]}"
            )
            continue
        consensus = matches[0]

        if row.finding_count != consensus["n_findings"]:
            violations.append(
                f"line {row.lineno}: 「{row.label}」行写「{row.finding_count} 条」finding，"
                f"与 {consensus['path'].name} 实算 {consensus['n_findings']} 条不符"
            )

        target_hash = consensus["consensus_hash"]
        sc_matches = [
            s for s in sc_records if target_hash and s["consensus_hash"] == target_hash
        ]
        if not sc_matches:
            violations.append(
                f"line {row.lineno}: 「{row.label}」行找不到绑定 {consensus['path'].name} 的 "
                f"sc-manifest artifact（consensus_artifact_hash={target_hash[:12]}…）"
            )
            continue
        if len(sc_matches) > 1:
            violations.append(
                f"line {row.lineno}: 「{row.label}」行绑定到 {len(sc_matches)} 个 sc-manifest "
                f"artifact，绑定歧义: {[str(m['path'].name) for m in sc_matches]}"
            )
            continue
        sc = sc_matches[0]

        if row.sc_count != sc["n_scs"]:
            violations.append(
                f"line {row.lineno}: 「{row.label}」行写「{row.sc_count} 条 SC」，"
                f"与 {sc['path'].name} 实算 {sc['n_scs']} 条不符"
            )

    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--body", required=True, help="PR body Markdown 文件路径")
    parser.add_argument(
        "--artifacts-dir",
        required=True,
        help="存放 consensus-r<N>.json / sc-manifest*.json 的目录",
    )
    args = parser.parse_args(argv)

    body_path = Path(args.body)
    artifacts_dir = Path(args.artifacts_dir)
    if not body_path.is_file():
        print(f"check_pr_body_counts: body 不存在: {body_path}", file=sys.stderr)
        return 2
    if not artifacts_dir.is_dir():
        print(f"check_pr_body_counts: artifacts-dir 不存在: {artifacts_dir}", file=sys.stderr)
        return 2

    violations = check(body_path, artifacts_dir)
    if violations:
        for violation in violations:
            print(f"check_pr_body_counts: {violation}", file=sys.stderr)
        print(
            f"check_pr_body_counts: FAIL — {len(violations)} 条违规 ({body_path})",
            file=sys.stderr,
        )
        return 1
    print(f"check_pr_body_counts: OK — 复核轨迹表计数与机器来源一致 ({body_path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
