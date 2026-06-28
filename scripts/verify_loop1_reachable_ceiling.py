#!/usr/bin/env python3
"""verify_loop1_reachable_ceiling.py — SC-7/SC-8 机器可判验收器（循环1 内容车道质量计划）.

把"满仓达到可达上限 = 够格全过 + 该拒全拒"压成可执行、可复验的判定，对照 P1 产出的
decision-manifest（每条 {fixture_id, verdict, expected_after_p2}）检查 calibration factory.json。

factory.json `passed` 字段语义（实测）：`passed=True` 表示**达成了该样本的 expected 结果**——
对 expected=ingest 的样本是"已入库"，对 expected=reject 的样本是"已正确拒"
（此时 `final_status=="rejected_correctly"`）。`passed=False` 表示**未达成**（含正样本 repair 不收敛
这类失败）。故"正确拒"绝不能用 `passed==False` 判（那会把"本该入库却失败"误当"正确拒"）。

两种模式：
  --mode coverage（SC-7，manifest 刚产出、P2 未跑）：只查
    (a) verdict↔expected_after_p2 不变量（救/调→ingest、拒→reject）；
    (b) manifest 覆盖 factory 里**全部失败正样本**（expected=ingest 且 passed=False）——不漏审。
  --mode outcome（SC-8，默认，P2 重跑后）：查
    (a) 同上不变量；
    (b) 每条 manifest entry 达成其 expected_after_p2：
        ingest → 该 factory 样本 passed==True；
        reject → passed==True 且 final_status 属"正确拒"集合（不是 pipeline 失败）；
    (c) negative_reject_rate == 1.0。

用法:
    python3 scripts/verify_loop1_reachable_ceiling.py \\
        --factory <factory.json> --decisions <decision-manifest.json> [--mode outcome|coverage]
    python3 scripts/verify_loop1_reachable_ceiling.py --self-test

退出码: 0 全符合 / 1 有偏差(fail-visible) / 2 用法或文件/JSON 错误
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

_FIXTURE_NUM_RE = re.compile(r"(\d+)")

# final_status 表示"被质量门正确拒"的取值（非 pipeline 失败 / 非 repair 不收敛）。
_CLEAN_REJECT_STATUSES = {"rejected_correctly", "rejected", "non_knowledge_rejected"}


def _index_keys(entry: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    if entry.get("index") is not None:
        keys.add(str(entry["index"]))
    if entry.get("url"):
        keys.add(str(entry["url"]))
    return keys


def _norm_fixture_id(fixture_id: Any) -> set[str]:
    raw = str(fixture_id)
    cands = {raw}
    m = _FIXTURE_NUM_RE.search(raw)
    if m:
        cands.add(str(int(m.group(1))))
    return cands


def _verdict_expected_consistent(verdict: Any, expected: Any) -> bool:
    """救/调 必映射 ingest；拒 必映射 reject。其它一律视为不一致。"""
    if verdict == "拒":
        return expected == "reject"
    if verdict in ("救", "调"):
        return expected == "ingest"
    return False


def check_reachable_ceiling(
    factory: dict[str, Any], decisions: dict[str, Any], mode: str = "outcome"
) -> tuple[bool, list[str]]:
    failures: list[str] = []

    entries = factory.get("entries")
    if not isinstance(entries, list):
        return False, ["factory.json 缺 'entries' 数组"]
    decision_entries = decisions.get("entries")
    if not isinstance(decision_entries, list) or not decision_entries:
        return False, ["decision-manifest 缺非空 'entries' 数组"]

    by_key: dict[str, dict[str, Any]] = {}
    for e in entries:
        if isinstance(e, dict):
            for k in _index_keys(e):
                by_key[k] = e

    # (a) verdict↔expected_after_p2 不变量（两模式都查）
    manifest_keys: set[str] = set()
    for d in decision_entries:
        if not isinstance(d, dict):
            failures.append(f"decision entry 非对象: {d!r}")
            continue
        fid, verdict, expected = d.get("fixture_id"), d.get("verdict"), d.get("expected_after_p2")
        if verdict not in ("救", "调", "拒"):
            failures.append(f"fixture_id={fid!r} verdict 非法: {verdict!r}（须 救/调/拒）")
        if expected not in ("ingest", "reject"):
            failures.append(f"fixture_id={fid!r} expected_after_p2 非法: {expected!r}")
        if not _verdict_expected_consistent(verdict, expected):
            want = "reject" if verdict == "拒" else "ingest"
            failures.append(
                f"fixture_id={fid!r} 不变量违反: verdict={verdict!r} 须映射 "
                f"expected_after_p2={want!r}，实际 {expected!r}"
            )
        manifest_keys |= _norm_fixture_id(fid)

    if mode == "coverage":
        # (b) manifest 必须覆盖全部失败正样本（expected=ingest 且 passed=False）
        for e in entries:
            if e.get("expected") == "ingest" and not bool(e.get("passed", False)):
                if not (_index_keys(e) & manifest_keys):
                    failures.append(
                        f"失败正样本 index={e.get('index')!r} url={e.get('url')!r} "
                        "未被 decision-manifest 覆盖（漏审）"
                    )
        return (not failures), failures

    if mode != "outcome":
        return False, [f"未知 mode: {mode!r}（须 outcome|coverage）"]

    # (b) 每条 manifest entry 达成 expected_after_p2
    for d in decision_entries:
        if not isinstance(d, dict):
            continue
        fid, expected = d.get("fixture_id"), d.get("expected_after_p2")
        match = next((by_key[c] for c in _norm_fixture_id(fid) if c in by_key), None)
        if match is None:
            failures.append(f"fixture_id={fid!r} 在 factory.json 中找不到对应样本")
            continue
        passed = bool(match.get("passed", False))
        final_status = match.get("final_status")
        if expected == "ingest":
            if not passed:
                seg = match.get("failure_segment")
                failures.append(
                    f"fixture_id={fid!r} P1 判 ingest 但 factory.passed=False "
                    f"(final_status={final_status!r}, failure_segment={seg!r})"
                )
        elif expected == "reject":
            # 正确拒 = passed=True 且 final_status 属"正确拒"集合；
            # 绝不接受 passed=False（那是失败而非正确拒，会把 repair 不收敛误当 reject）。
            if not passed or final_status not in _CLEAN_REJECT_STATUSES:
                failures.append(
                    f"fixture_id={fid!r} P1 判 reject，但非'正确拒'状态 "
                    f"(passed={passed!r}, final_status={final_status!r}；"
                    f"须 passed=True 且 final_status∈{sorted(_CLEAN_REJECT_STATUSES)})"
                )

    # (c) 负样本 100% 拒
    nrr = factory.get("negative_reject_rate")
    if nrr != 1.0:
        failures.append(f"negative_reject_rate={nrr!r} != 1.0（负样本未 100% 拒）")

    return (not failures), failures


def _load(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------- #
# Self-test（内联 fixture 用 factory 真实 schema：reject 样本 passed=True + rejected_correctly）
# --------------------------------------------------------------------------- #

def _self_test() -> int:
    def e(idx: int, exp: str, passed: bool, fs: str, **kw: Any) -> dict[str, Any]:
        return {"index": idx, "url": f"u{idx}", "expected": exp,
                "passed": passed, "final_status": fs, **kw}

    def m(fid: Any, verdict: str, exp: str) -> dict[str, Any]:
        return {"fixture_id": fid, "verdict": verdict, "expected_after_p2": exp}

    factory = {
        "negative_reject_rate": 1.0,
        "entries": [
            e(4, "ingest", True, "ingested"),
            e(11, "reject", True, "rejected_correctly"),
            e(7, "ingest", False, "failed", failure_segment="repair_loop"),
        ],
    }
    ok_dec = {"entries": [m("factory-04", "救", "ingest"), m(11, "拒", "reject")]}
    cases: list[tuple[str, dict, dict, str, bool]] = [
        ("outcome pass: ingest + reject 正确拒(passed=True) + nrr=1",
         factory, ok_dec, "outcome", True),
        ("outcome FAIL: reject 项是 repair 失败(passed=False)",
         factory, {"entries": [m(7, "拒", "reject")]}, "outcome", False),
        ("outcome FAIL: 不变量 verdict=拒 但 expected=ingest",
         factory, {"entries": [m(11, "拒", "ingest")]}, "outcome", False),
        ("outcome FAIL: nrr != 1.0",
         {**factory, "negative_reject_rate": 0.5}, ok_dec, "outcome", False),
        ("outcome FAIL: ingest 项 passed=False",
         factory, {"entries": [m(7, "调", "ingest")]}, "outcome", False),
        ("coverage pass: 失败正样本 idx7 被覆盖",
         factory, {"entries": [m(7, "救", "ingest")]}, "coverage", True),
        ("coverage FAIL: 失败正样本 idx7 未覆盖",
         factory, {"entries": [m(4, "救", "ingest")]}, "coverage", False),
    ]
    all_ok = True
    for name, fac, dec, mode, expect_ok in cases:
        ok, fails = check_reachable_ceiling(fac, dec, mode)
        status = "PASS" if ok == expect_ok else "FAIL"
        if ok != expect_ok:
            all_ok = False
        print(f"[{status}] {name}  (ok={ok}, failures={len(fails)})")
    print("SELF_TEST_PASSED" if all_ok else "SELF_TEST_FAILED")
    return 0 if all_ok else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="SC-7/SC-8 验收器：对照 decision-manifest 复验 factory.json。"
    )
    parser.add_argument("--factory", help="calibration factory.json 路径")
    parser.add_argument("--decisions", help="P1 decision-manifest.json 路径")
    parser.add_argument("--mode", choices=["outcome", "coverage"], default="outcome",
                        help="outcome=SC-8 P2 后查结果；coverage=SC-7 查不变量+覆盖")
    parser.add_argument("--self-test", action="store_true", help="跑内联自测，不读外部文件")
    args = parser.parse_args(argv[1:])

    if args.self_test:
        return _self_test()
    if not args.factory or not args.decisions:
        print("错误: 需同时给 --factory 与 --decisions（或用 --self-test）", file=sys.stderr)
        return 2
    try:
        factory = _load(args.factory)
        decisions = _load(args.decisions)
    except FileNotFoundError as e:
        print(f"错误: 文件不存在 — {e}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"错误: JSON 解析失败 — {e}", file=sys.stderr)
        return 2

    ok, failures = check_reachable_ceiling(factory, decisions, args.mode)
    if ok:
        n = len(decisions.get("entries", []))
        print(f"REACHABLE_CEILING_PASSED (mode={args.mode}, {n} 条定性全部符合)")
        return 0
    print(f"REACHABLE_CEILING_FAILED (mode={args.mode})")
    for f in failures:
        print(f"  - {f}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
