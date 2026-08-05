#!/usr/bin/env python3
"""check_mutation_log.py — 变异证据口径的机器 owner（SC-R6-05）。

「红集必须写明选择范围、禁止裸 exact」这条规矩此前只写在 mutation-log-r4.md 的正文里，
没有任何机制让下一轮遵守 —— R5 轮 lead 自己第一个违反（dispatch-record-r5 里只有
`exact`、零处「选择范围」）。判据没有 owner 就等于没有判据，本脚本把它接成机器闸。

校验对象：一轮修复的变异对照日志（`_tmp/pr-review/mutation-log-r<N>.md`）。

判据（全部 fail-closed，任一违规即非零退出并指名条目）：
  ① 日志文件存在；
  ② `--sc-manifest` 派生的每条 SC 在日志中有条目（含该 SC id 的 markdown 标题行）；
  ③ 每条条目内「选择范围」「预测红集」「实测红集」三个字段都存在且非空；
  ④ 任何声称红集等价的表述都必须同行带「选择范围」。
     等价声称词（机械对账）：exact、exactly、精确、恰好、恰为、完全一致。

SC id 全集只从 manifest 派生；可选 `--sc-ids` 仅用于兼容调用方自检，若提供则必须
与 manifest 派生的逗号清单逐字相等，不能缩小、增补或重排。

用法：
    python3 scripts/check_mutation_log.py --log _tmp/pr-review/mutation-log-r6.md \
        --sc-manifest _tmp/pr-review/sc-manifest-r6.json

退出码：0 = 合规；1 = 判据违规；2 = 日志文件不存在（①）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REQUIRED_FIELDS = ("选择范围", "预测红集", "实测红集")
EQUIVALENCE_CLAIMS = ("exact", "exactly", "精确", "恰好", "恰为", "完全一致")
_RE_EQUIVALENCE_CLAIM = re.compile(
    "|".join(
        rf"(?<![A-Za-z0-9_]){re.escape(claim)}(?![A-Za-z0-9_])"
        if claim.isascii()
        else re.escape(claim)
        for claim in EQUIVALENCE_CLAIMS
    ),
    re.IGNORECASE,
)
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


def load_manifest_sc_ids(manifest_path: Path) -> list[str]:
    """从 SC manifest 的 `scs[*].id` 派生非空、无重复的完整 id 清单。"""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 SC manifest: {exc}") from exc

    if not isinstance(manifest, dict) or not isinstance(manifest.get("scs"), list):
        raise ValueError("SC manifest 缺少数组字段 `scs`")

    sc_ids: list[str] = []
    for index, item in enumerate(manifest["scs"]):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError(f"SC manifest 的 scs[{index}].id 缺失或不是字符串")
        sc_id = item["id"]
        if not sc_id:
            raise ValueError(f"SC manifest 的 scs[{index}].id 为空")
        if sc_id in sc_ids:
            raise ValueError(f"SC manifest 含重复 id: {sc_id}")
        sc_ids.append(sc_id)

    if not sc_ids:
        raise ValueError("SC manifest 的 `scs` 为空")
    return sc_ids


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
        claim = _RE_EQUIVALENCE_CLAIM.search(line)
        if claim and "选择范围" not in line:
            violations.append(
                f"line {lineno}: 无范围的等价声称（含裸 exact）`{claim.group(0)}` —— "
                "红集表述必须同行写明选择范围（判据④）: "
                f"{line.strip()[:80]}"
            )
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True, help="变异对照日志路径")
    parser.add_argument(
        "--sc-manifest", required=True, help="当轮 SC manifest 路径（SC 全集的唯一来源）"
    )
    parser.add_argument(
        "--sc-ids", help="可选的逗号分隔 SC id 清单；必须与 manifest 派生清单逐字相等"
    )
    args = parser.parse_args(argv)

    log_path = Path(args.log)
    if not log_path.is_file():
        print(f"check_mutation_log: 日志不存在（判据①）: {log_path}", file=sys.stderr)
        return 2

    manifest_path = Path(args.sc_manifest)
    try:
        sc_ids = load_manifest_sc_ids(manifest_path)
    except ValueError as exc:
        print(f"check_mutation_log: {exc}", file=sys.stderr)
        return 1

    expected_sc_ids = ",".join(sc_ids)
    if args.sc_ids is not None and args.sc_ids != expected_sc_ids:
        caller_sc_ids = [item.strip() for item in args.sc_ids.split(",") if item.strip()]
        missing = [sc_id for sc_id in sc_ids if sc_id not in caller_sc_ids]
        extra = [sc_id for sc_id in caller_sc_ids if sc_id not in sc_ids]
        details: list[str] = []
        if missing:
            details.append(f"缺失 SC id: {','.join(missing)}")
        if extra:
            details.append(f"多余 SC id: {','.join(extra)}")
        if not details:
            details.append("SC id 顺序或文本不一致")
        print(
            "check_mutation_log: --sc-ids 与 manifest 派生清单不一致（"
            + "；".join(details)
            + "）",
            file=sys.stderr,
        )
        return 1

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
