#!/usr/bin/env python3
"""harvest 术语单一化机器扫描(规范正文层面的硬约束)。

规格 (5f2):sc-asm-bootstrap 与 sc-asm-anchors 的 change/holds/expect 三字段中,
剥离三类豁免区之后,固定禁用词表的命中数必须为 0。

唯一入口:
    check_terminology.py --manifest <path>

输入:manifest JSON(task-manifest.json 同构,含 scs[];每项含 id/change/holds/expect),
只扫描 id ∈ {sc-asm-bootstrap, sc-asm-anchors} 的 change/holds/expect 三字段。

剥离次序(写死,不可调换):
  ① `<!-- banned-vocab -->` … `<!-- /banned-vocab -->` 标记对内全部内容
     (禁用词表与「已推翻结构」的历史记录所在地,本身豁免)
  ② `<!-- reject-list -->` … `<!-- /reject-list -->` 标记对内全部内容
     (argv 层「点名拒收」清单,必须点名才能拒收)
  ③ 以 `(GPT 质询` 或 `(GPT 终审` 开头的括注 —— 按括号配对剥离,支持嵌套

随后对固定禁用词表扫描,命中数非 0 即 exit 1 并逐条打印
命中词 / SC id / 字段 / 前后各 80 字上下文。

词表(与规格 (5f2) 写死的那张表逐项相等;加词必须同步改规格与测试):
"""

import json
import sys

# 固定禁用词表 —— 与规格 (5f2) 写死的表逐项相等(测试断言词表完备性)
WORDS = [
    "active",
    "epoch-state",
    "deployment-state",
    "独立吊销链",
    "密钥吊销链",
    "指针切换",
    "commit 记录",
    "读 commit",
    "对 commit 做 replace",
    "部署状态里",
]

SC_IDS = ["sc-asm-bootstrap", "sc-asm-anchors"]
FIELDS = ["change", "holds", "expect"]

MARK_OPEN = "<!-- banned-vocab -->"
MARK_CLOSE = "<!-- /banned-vocab -->"
REJECT_OPEN = "<!-- reject-list -->"
REJECT_CLOSE = "<!-- /reject-list -->"


def strip_marks(text: str, open_mark: str, close_mark: str) -> str:
    """剥离指定标记对内全部内容(标记对不嵌套)。"""
    out = []
    i = 0
    while True:
        start = text.find(open_mark, i)
        if start < 0:
            out.append(text[i:])
            break
        end = text.find(close_mark, start + len(open_mark))
        if end < 0:
            # 未闭合的标记对:从开启处截断(视为剥离至文末)
            out.append(text[i:start])
            break
        out.append(text[i:start])
        i = end + len(close_mark)
    return "".join(out)


def strip_gpt_annotations(text: str) -> str:
    """剥离 (GPT 质询…)/(GPT 终审…) 括注,按括号配对、支持嵌套。

    括注从 `(GPT 质询` 或 `(GPT 终审` 开始;内容中的嵌套括号计入深度,
    直到深度归零的 `)` 结束。嵌套内不再以 GPT 开头也按普通括号计数——
    即剥离器做的是括号配对,不是贪婪/非贪婪文本匹配。
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("(GPT 质询", i) or text.startswith("(GPT 终审", i):
            depth = 1
            j = i + 1
            while j < n and depth > 0:
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                j += 1
            i = j  # 括注整体跳过(含未闭合到文末的情况)
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def strip_all(text: str) -> str:
    """剥离次序写死:① banned-vocab ② reject-list ③ GPT 括注。"""
    text = strip_marks(text, MARK_OPEN, MARK_CLOSE)
    text = strip_marks(text, REJECT_OPEN, REJECT_CLOSE)
    return strip_gpt_annotations(text)


def scan(text: str) -> list[tuple[str, int]]:
    """返回 [(命中词, 命中位置)]。"""
    hits = []
    for word in WORDS:
        start = 0
        while True:
            pos = text.find(word, start)
            if pos < 0:
                break
            hits.append((word, pos))
            start = pos + len(word)
    return hits


def main() -> int:
    argv = sys.argv[1:]
    if argv in (["--help"], ["-h"]):
        print(
            "usage: check_terminology.py --manifest <path>\n\n"
            "术语单一化机器扫描:剥离三类豁免区后,固定禁用词表命中数必须为 0。\n"
            "输入为 task-manifest.json 同构 JSON(含 scs[])。",
            end="",
        )
        return 0
    if len(argv) != 2 or argv[0] != "--manifest":
        print("harvest terminology: 用法错误:--manifest <path>;--help 查看用法", file=sys.stderr)
        return 2
    manifest_path = argv[1]
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"harvest terminology: manifest 无法读取或解析:{manifest_path}({e})", file=sys.stderr)
        return 2
    scs = manifest if isinstance(manifest, list) else manifest.get("scs", [])
    total = 0
    for sc in scs:
        sc_id = sc.get("id") if isinstance(sc, dict) else None
        if sc_id not in SC_IDS:
            continue
        for field in FIELDS:
            body = strip_all(sc.get(field, ""))
            hits = scan(body)
            for word, pos in hits:
                total += 1
                ctx = body[max(0, pos - 80):pos + 80].replace("\n", " ")
                print(f"命中 {word} @ {sc_id}.{field} | …{ctx}…")
    if total:
        print(f"总命中: {total} ❌ 仍红(术语未统一,禁止提交)", file=sys.stderr)
        return 1
    print("总命中: 0 ✅ 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
