"""故障签名记录与升级。

公开函数:
    record_failure(failures_path, error_class, node, loop="loop1") -> dict

- 按 failure-signature.schema.json 格式写入 failures.jsonl。
- 同签名的 count 累计；第 3 次出现时写入 escalation 标记行。
- 返回值包含 count 与 escalated 标志，供调用方判断升级状态。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 具名常量
# ---------------------------------------------------------------------------

ESCALATION_THRESHOLD = 3  # 第 3 次出现触发升级
FAILURES_FILENAME = "failures.jsonl"


def _now_utc() -> str:
    """ISO-8601 UTC 时间戳。"""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_failures(path: Path) -> list[dict]:
    """解析 failures.jsonl 为 dict 列表，跳过空行与损坏行。"""
    if not path.exists():
        return []
    rows: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def record_failure(
    failures_path: str,
    error_class: str,
    node: str,
    loop: str = "loop1",
) -> dict:
    """记录一次故障签名。

    同签名多次出现时累计 count；达到 ESCALATION_THRESHOLD(3) 时
    写入一条 escalation 标记行（保留前两次记录不动）。

    Args:
        failures_path: failures.jsonl 文件路径。
        error_class: 故障分类枚举（见 failure-signature.schema.json）。
        node: 故障节点标识。
        loop: 故障所属循环（默认 loop1）。

    Returns:
        dict: {"count": int, "escalated": bool, "signature": str}
    """
    path = Path(failures_path)
    tss = _now_utc()

    # 读已有记录
    existing = _parse_failures(path)

    # 统计此签名的历史 count（从普通签名行计数，跳过 escalation 行）
    current_count = 0
    for row in existing:
        if (
            row.get("loop") == loop
            and row.get("node") == node
            and row.get("error_class") == error_class
            and "escalation" not in row  # 不重复计 escalation 本身
        ):
            current_count += 1

    new_count = current_count + 1

    # 写入新的签名记录行
    record: dict = {
        "loop": loop,
        "node": node,
        "error_class": error_class,
        "count": new_count,
        "ts": tss,
    }
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")

    escalated = new_count >= ESCALATION_THRESHOLD

    # 达到升级阈值时追加 escalation 标记行
    if escalated:
        escalation: dict = {
            "loop": loop,
            "node": node,
            "error_class": error_class,
            "count": new_count,
            "ts": tss,
            "escalation": {
                "escalated": True,
                "escalated_at": tss,
                "reason": f"同一签名第 {new_count} 次出现，触发升级规则",
            },
        }
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(escalation, sort_keys=True, ensure_ascii=False) + "\n")

    return {
        "count": new_count,
        "escalated": escalated,
        "signature": f"{loop}/{node}/{error_class}",
    }
