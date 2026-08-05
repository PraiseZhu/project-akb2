#!/usr/bin/env python3
"""check_mutation_log.py — 变异证据口径的机器 owner（SC-R6-05）。

「红集必须写明选择范围、禁止裸 exact」这条规矩此前只写在 mutation-log-r4.md 的正文里，
没有任何机制让下一轮遵守 —— R5 轮 lead 自己第一个违反（dispatch-record-r5 里只有
`exact`、零处「选择范围」）。判据没有 owner 就等于没有判据，本脚本把它接成机器闸。

校验对象：一轮修复的变异对照日志（`_tmp/pr-review/mutation-log-r<N>.md`）。

判据（全部 fail-closed，任一违规即非零退出并指名条目）：
  ① 日志文件存在；
  ② `--sc-manifest` 派生的每条 SC 在日志中有条目（含该 SC id 的 markdown 标题行）；
  ③ 结构规则（R8 判据归一）：每条 SC 条目内每个「实测红集」都界定一个变异窗口
     （从上一个「实测红集」之后到本行为止），该窗口必须同时含非空「选择范围」与
     「预测红集」；条目至少有一个「实测红集」。选择范围与红集因此**逐变异**绑定。

R8（SC-R8-02，收敛检查点）判据从**词法**改成**结构**：早先有一条词法判据用有限词表
（exact/精确/恰好/…）去检测「这行是不是在声称等价」，而自然语言的等价表述没有上界——
R6 补英文、R7 补中文，R7 复审仍能用「相等/相同/匹配/一致/一一对应/precisely/identical」
绕过。有限词表永远无法兑现 docstring 声称的「任何等价声称」。现在**不再检测措辞**：只要
每个实测红集所在的变异窗口都声明了选择范围（③），无论它用什么词描述红集，「无范围的
等价声称」这个失败形态在结构上就不可能存在。词表整体删除，docstring 的判据描述随之
变成可兑现的真话；附带好处：R6/R7 两轮咬中日志自身元文本（「引用了等价声称词」）的
误报模式一并消失。

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

# 结构判据的三个字段。R8 起没有独立的「等价声称词表」——判据从词法改成结构，
# 「实测红集」界定变异窗口，「选择范围」「预测红集」必须落在同一窗口内（见模块 docstring）。
SCOPE_FIELD = "选择范围"
PREDICTED_FIELD = "预测红集"
ACTUAL_FIELD = "实测红集"
REQUIRED_FIELDS = (SCOPE_FIELD, PREDICTED_FIELD, ACTUAL_FIELD)
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


def _has_field(body: list[str], field: str) -> bool:
    return any(_FIELD_RE[field].search(line) for line in body)


def check(log_path: Path, sc_ids: list[str]) -> list[str]:
    violations: list[str] = []
    lines = log_path.read_text(encoding="utf-8").splitlines()

    entries = split_entries(lines, sc_ids)
    for sc_id in sc_ids:
        if sc_id not in entries:
            violations.append(f"{sc_id}: 日志中无该 SC 的条目（判据②）")
            continue
        body = entries[sc_id]
        # 结构判据③（R8 归一）：按「实测红集」把条目切成变异窗口，逐窗口要求同时有
        # 选择范围与预测红集。这样选择范围与红集是**逐变异**绑定的，不再靠扫描等价声称词。
        window: list[str] = []
        window_index = 0
        saw_actual = False
        for line in body:
            window.append(line)
            if _FIELD_RE[ACTUAL_FIELD].search(line):
                saw_actual = True
                window_index += 1
                if not _has_field(window, SCOPE_FIELD):
                    violations.append(
                        f"{sc_id}: 第 {window_index} 个「{ACTUAL_FIELD}」所在变异窗口缺"
                        f"非空「{SCOPE_FIELD}」（判据③：选择范围必须逐变异声明）"
                    )
                if not _has_field(window, PREDICTED_FIELD):
                    violations.append(
                        f"{sc_id}: 第 {window_index} 个「{ACTUAL_FIELD}」所在变异窗口缺"
                        f"非空「{PREDICTED_FIELD}」（判据③）"
                    )
                window = []
        if not saw_actual:
            violations.append(
                f"{sc_id}: 条目内无「{ACTUAL_FIELD}」，无法核对变异结果（判据③）"
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
