"""故障签名记录与升级。

公开函数:
    record_failure(failures_path, error_class, node, loop="loop1") -> dict

- 按 failure-signature.schema.json 格式写入 failures.jsonl。
- 同签名的 count 累计；第 3 次出现时写入 escalation 标记行。
- 返回值包含 count 与 escalated 标志，供调用方判断升级状态。

SC-R2-11② 审计连带修复（2026-08-04）：
    `failures.jsonl` 与 `quality-ledger.jsonl` 同住机器本地状态目录，且 `node`
    字段会带上入库源 URL（`intake.extract.<url>`，URL 可能含 query 里的凭证样式
    参数）。原先用普通 `open(path, "a")` → umask 022 下落成 0644。权限判据只有一个
    owner：`loops.ledger.open_append_secure`（首次创建即 0600，已存在的更宽文件收紧），
    本模块不自建第二份 chmod 逻辑。

SC-R4-03 修复（2026-08-05）：
    上一轮把「node 带凭证样式参数」这件事写进了本 docstring，却只修了不变量的**权限**
    那一半 —— 0600 落地了，**脱敏没有**：`node` 从未经过 redactor，含 `?access_token=…`
    的入库源 URL 原样写进 `failures.jsonl` 三处（签名行 / escalation 行 / 返回的
    `signature`）。现在 `record_failure` 在入口处一次性把 `node` 过
    `common.http_retry.redact_provider_text`（**同一个 chokepoint，不新建第二套规则**——
    它已能处理 URL query 的 `token=` 形态）。

    ⚠ 语义副作用（已知并接受）：`node` 参与 `signature = f"{loop}/{node}/{error_class}"`，
    脱敏改变签名身份 → 旧账本里的旧签名与新签名对不上，**去重/升级计数会一次性重置**。
    代价可接受，且方向是对的：此前仅 query token 不同的同一 URL 会被算成不同签名而
    永不触发升级，脱敏后它们归并为同一签名。去重逻辑本身不变（见
    `tests/loops/test_ledger_permissions.py::test_failures_node_field_is_redacted`
    的计数断言：同源归并到 3 并升级，异源仍各自从 1 起算）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ..common.http_retry import redact_provider_text
from .ledger import open_append_secure

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
        node: 故障节点标识；可能带外部入库源 URL（含 query 里的凭证样式参数），
            **在此处一次性脱敏**，后续所有消费点用的都是脱敏值。
        loop: 故障所属循环（默认 loop1）。

    Returns:
        dict: {"count": int, "escalated": bool, "signature": str}（signature 中的
        node 已脱敏）
    """
    path = Path(failures_path)
    tss = _now_utc()

    # 脱敏在**入口**做，不在三个写入点各做一次：计数比对、签名行、escalation 行与返回的
    # signature 必须看到同一个 node，否则计数会与落盘值对不上（同一不变量再次只落地一半）。
    node = redact_provider_text(node)

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

    with open_append_secure(path) as fh:
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
        with open_append_secure(path) as fh:
            fh.write(json.dumps(escalation, sort_keys=True, ensure_ascii=False) + "\n")

    return {
        "count": new_count,
        "escalated": escalated,
        "signature": f"{loop}/{node}/{error_class}",
    }
