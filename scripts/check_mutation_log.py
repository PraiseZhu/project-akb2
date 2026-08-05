#!/usr/bin/env python3
"""check_mutation_log.py — 变异证据口径的机器 owner（SC-R6-05）。

「红集必须写明选择范围、禁止裸 exact」这条规矩此前只写在 mutation-log-r4.md 的正文里，
没有任何机制让下一轮遵守 —— R5 轮 lead 自己第一个违反（dispatch-record-r5 里只有
`exact`、零处「选择范围」）。判据没有 owner 就等于没有判据，本脚本把它接成机器闸。

校验对象：一轮修复的变异对照日志（`_tmp/pr-review/mutation-log-r<N>.md`）。

判据（全部 fail-closed，任一违规即非零退出并指名条目）：
  ① 日志文件存在；
  ② `--sc-ids` 里的每条 SC 在日志中有条目（含该 SC id 的 markdown 标题行）；
  ③ 每条条目内「选择范围」「预测红集」「实测红集」三个字段都存在且非空；
  ④ 全文禁止**无范围的裸 exact**：出现独立单词 `exact` 的行必须同行带「选择范围」
     （"精确 N 条"式的无范围表述正是 R4 被审出的证据失真形态）。

用法：
    python3 scripts/check_mutation_log.py --log _tmp/pr-review/mutation-log-r6.md \
        --sc-ids SC-R6-01,SC-R6-02

退出码：0 = 合规；1 = 判据违规；2 = 日志文件不存在（①）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REQUIRED_FIELDS = ("选择范围", "预测红集", "实测红集")
# 独立单词 exact（大小写不敏感）；"exactly" 这类包含式不算裸 exact
_RE_BARE_EXACT = re.compile(r"\bexact\b", re.IGNORECASE)
_RE_HEADING = re.compile(r"^#{1,6}\s+(.*)$")
# 字段行：`- 选择范围: …` / `选择范围：…`（冒号中英皆可），内容须非空
_FIELD_RE = {
    field: re.compile(rf"{field}\s*[:：]\s*(\S.*)$") for field in REQUIRED_FIELDS
}


def split_entries(lines: list[str], sc_ids: list[str]) -> dict[str, list[str]]:
    """按含 SC id 的标题行切条目：标题行起，到下一个标题行止。"""
    entries: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        heading = _RE_HEADING.match(line)
        if heading:
            title = heading.group(1)
            current = next((sc for sc in sc_ids if sc in title), None)
            if current is not None:
                entries.setdefault(current, [])
            continue
        if current is not None:
            entries[current].append(line)
    return entries


def check(log_path: Path, sc_ids: list[str]) -> list[str]:
    violations: list[str] = []
    lines = log_path.read_text(encoding="utf-8").splitlines()

    entries = split_entries(lines, sc_ids)
    for sc_id in sc_ids:
        if sc_id not in entries:
            violations.append(f"{sc_id}: 日志中无该 SC 的条目（判据②）")
            continue
        body = entries[sc_id]
        for field in REQUIRED_FIELDS:
            if not any(_FIELD_RE[field].search(line) for line in body):
                violations.append(f"{sc_id}: 字段「{field}」缺失或为空（判据③）")

    for lineno, line in enumerate(lines, start=1):
        if _RE_BARE_EXACT.search(line) and "选择范围" not in line:
            violations.append(
                f"line {lineno}: 无范围的裸 exact —— 红集表述必须同行写明选择范围（判据④）: "
                f"{line.strip()[:80]}"
            )
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True, help="变异对照日志路径")
    parser.add_argument(
        "--sc-ids", required=True, help="逗号分隔的 SC id 清单（每条都必须有条目）"
    )
    args = parser.parse_args(argv)

    log_path = Path(args.log)
    sc_ids = [s.strip() for s in args.sc_ids.split(",") if s.strip()]
    if not sc_ids:
        print("check_mutation_log: --sc-ids 为空", file=sys.stderr)
        return 1
    if not log_path.is_file():
        print(f"check_mutation_log: 日志不存在（判据①）: {log_path}", file=sys.stderr)
        return 2

    violations = check(log_path, sc_ids)
    if violations:
        for violation in violations:
            print(f"check_mutation_log: {violation}", file=sys.stderr)
        print(
            f"check_mutation_log: FAIL — {len(violations)} 条违规 ({log_path})",
            file=sys.stderr,
        )
        return 1
    print(f"check_mutation_log: OK — {len(sc_ids)} 条 SC 证据口径合规 ({log_path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
