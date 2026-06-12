"""定点修复循环 — 模板化修复指令 → 定点重写 → diff 校验 → A+B 复检。

核心约束:
    - MAX_REPAIR_ROUNDS = 3（具名常量，宪法 P1/P4）
    - 修复指令模板化拼接，非自由生成
    - diff 校验: 未点名小节逐字节不变（违规即丢弃该轮重写）
    - 3 轮不收敛 → fail-closed: 留候选区 + 落 distill_no_converge 故障签名
    - 修复模型: DeepSeek-V4-Pro（经 llm_router，reasoning_effort=max）

修复流程:
    1. 缺陷清单 → 模板化拼接修复指令
    2. DeepSeek 定点重写被点名小节
    3. diff 校验: 未点名小节逐字节不变
    4. 重新过 A 层检查
    5. A 通过后重新过 B 层评分
    6. 仍有缺陷 → 下一轮（最多 3 轮）
    7. 3 轮不收敛 → fail-closed 签名落盘
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from src.akb2.loops.signatures import record_failure
from src.akb2.quality.checks_structural import (
    Defect,
    _extract_body_text,
    _find_section_boundaries,
    _get_section_text,
    run_structural_checks,
)
from src.akb2.quality.grader import run_grader_with_fixture

# 只读依赖
from src.akb2.quality.llm_router import RouterConfigError, route_llm

# ---------------------------------------------------------------------------
# 具名常量（宪法 P1/P4: 有界 + 预算显式）
# ---------------------------------------------------------------------------

MAX_REPAIR_ROUNDS: int = 3
REPAIR_MODEL = "deepseek-v4-pro"
REPAIR_TIMEOUT_S: float = 120.0


# ---------------------------------------------------------------------------
# 修复指令模板
# ---------------------------------------------------------------------------

REPAIR_INSTRUCTION_TEMPLATE = """你是一个知识库内容编辑。请对以下候选页进行**定点修复**。

## 修复规则（必须严格遵守）

1. **只修改被点名的小节**——以下"待修复缺陷"中 location 字段指向的小节。
2. **未点名的小节不得做任何修改**——包括标点、空格、换行。
3. 每个缺陷的 suggestion 字段给出了修复方向，请按其执行。
4. 修复后保持 markdown 格式不变。

## 待修复缺陷

{defects_text}

## 候选页原文

{candidate_text}

请输出修复后的**完整候选页**（含 frontmatter 和所有小节）。
只输出修复后的内容，不要添加任何解释或标记。"""


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _build_repair_instruction(defects: list[Defect]) -> str:
    """将缺陷清单拼接为模板化修复指令。

    每个缺陷格式: "- [{dimension}] {location}: {defect} → 建议: {suggestion}"
    """
    lines: list[str] = []
    for i, d in enumerate(defects, 1):
        lines.append(
            f"{i}. **[{d.dimension}]** {d.location}\n"
            f"   缺陷: {d.defect}\n"
            f"   建议: {d.suggestion}"
        )
    return "\n".join(lines)


def _get_named_sections(text: str) -> set[str]:
    """获取正文中所有被点名的小节标题集合。"""
    sections = _find_section_boundaries(text)
    return {title for title, _, _ in sections}


def _verify_unchanged_sections(
    original: str,
    repaired: str,
    named_sections: set[str],
) -> list[str]:
    """验证未点名小节是否逐字节不变。

    Args:
        original: 原始候选页
        repaired: 修复后候选页
        named_sections: 被点名的小节标题集合

    Returns:
        list[str]: 被改动了的未点名小节列表（空列表 = 全部未变）
    """
    changed: list[str] = []

    orig_body = _extract_body_text(original)
    rep_body = _extract_body_text(repaired)

    all_sections = _get_named_sections(orig_body) | _get_named_sections(rep_body)

    for section in all_sections:
        if section in named_sections:
            continue  # 点名小节允许改动

        orig_section_text = _get_section_text(orig_body, section)
        rep_section_text = _get_section_text(rep_body, section)

        if orig_section_text != rep_section_text:
            changed.append(section)

    return changed


def _compute_text_hash(text: str) -> str:
    """计算文本的 SHA-256 hex 摘要。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_utc() -> str:
    """ISO-8601 UTC 时间戳。"""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 公开函数
# ---------------------------------------------------------------------------


def run_repair_loop(
    candidate_text: str,
    a_defects: list[Defect],
    b_defects: list[Defect],
    failures_path: str,
    mock_route_llm: Any = None,
) -> dict[str, Any]:
    """执行定点修复循环。

    流程:
        1. 合并 A+B 缺陷清单
        2. 提取被点名小节
        3. 模板化拼接修复指令 → DeepSeek 定点重写
        4. diff 校验未点名小节逐字节不变
        5. 重新过 A 层检查
        6. A 通过后重新过 B 层评分
        7. 仍有缺陷 → 下一轮（最多 MAX_REPAIR_ROUNDS 轮）
        8. 3 轮不收敛 → fail-closed 签名落盘

    Args:
        candidate_text: 候选页全文
        a_defects: A 层缺陷清单
        b_defects: B 层缺陷清单
        failures_path: failures.jsonl 路径（不收敛时写签名）
        mock_route_llm: fixture LLM 路由函数（测试用），None 时用真 API

    Returns:
        dict: {
            "converged": bool,
            "rounds": int,
            "final_text": str | None,
            "final_defects": list[Defect],
            "signature_written": bool,
            "failure_signature": str | None,
        }
    """
    all_defects = a_defects + b_defects
    if not all_defects:
        return {
            "converged": True,
            "rounds": 0,
            "final_text": candidate_text,
            "final_defects": [],
            "signature_written": False,
            "failure_signature": None,
        }

    current_text = candidate_text
    current_defects = list(all_defects)

    for round_num in range(1, MAX_REPAIR_ROUNDS + 1):
        # 提取被点名的小节标题
        named_sections: set[str] = set()
        for d in current_defects:
            # location 可能是节标题或 frontmatter 字段
            loc = d.location
            # 尝试匹配节标题
            body = _extract_body_text(current_text)
            sections = _get_named_sections(body)
            for sec in sections:
                if sec in loc or loc in sec:
                    named_sections.add(sec)
                    break
            # 如果 location 指向 frontmatter，也需要标记
            if "frontmatter" in loc.lower():
                named_sections.add("__frontmatter__")

        # 构建修复指令
        instruction_text = _build_repair_instruction(current_defects)
        prompt = REPAIR_INSTRUCTION_TEMPLATE.format(
            defects_text=instruction_text,
            candidate_text=current_text,
        )

        messages = [
            {"role": "user", "content": prompt},
        ]

        # 调用 DeepSeek 定点重写
        try:
            if mock_route_llm is not None:
                response = mock_route_llm(
                    model_family=REPAIR_MODEL,
                    messages=messages,
                )
            else:
                response = route_llm(
                    model_family=REPAIR_MODEL,
                    messages=messages,
                )

            repaired_text = (
                response.content
                if isinstance(response.content, str)
                else json.dumps(response.content, ensure_ascii=False)
            )
        except (RouterConfigError, RuntimeError):
            # 修复调用失败，跳过本轮
            continue

        # diff 校验: 未点名小节逐字节不变
        changed_sections = _verify_unchanged_sections(
            current_text, repaired_text, named_sections
        )
        if changed_sections:
            # 违规: 丢弃本轮重写，不更新 current_text
            # 但仍继续下一轮（可能修复指令不同）
            continue

        # 更新当前文本
        current_text = repaired_text

        # 重新过 A 层
        new_a_defects = run_structural_checks(current_text)

        if new_a_defects:
            # A 层仍有缺陷，进入下一轮
            current_defects = new_a_defects
            continue

        # A 通过，过 B 层
        if mock_route_llm is not None:
            new_b_defects, _, suspended = run_grader_with_fixture(
                current_text, mock_route_llm
            )
        else:
            from src.akb2.quality.grader import run_grader

            new_b_defects, _, suspended = run_grader(current_text)

        if suspended:
            # B 层挂起 → fail-closed
            return _fail_closed(
                failures_path,
                current_text,
                current_defects,
                round_num,
                "grader_all_down",
            )

        current_defects = new_b_defects

        if not current_defects:
            # 收敛: 无缺陷
            return {
                "converged": True,
                "rounds": round_num,
                "final_text": current_text,
                "final_defects": [],
                "signature_written": False,
                "failure_signature": None,
            }

    # 3 轮耗尽后仍有缺陷 → fail-closed
    return _fail_closed(
        failures_path,
        current_text,
        current_defects,
        MAX_REPAIR_ROUNDS,
        "distill_no_converge",
    )


def _fail_closed(
    failures_path: str,
    final_text: str,
    remaining_defects: list[Defect],
    rounds: int,
    error_class: str,
) -> dict[str, Any]:
    """不收敛出口: 落故障签名 + 返回 fail-closed 结果。

    Args:
        failures_path: failures.jsonl 路径
        final_text: 最终稿件文本
        remaining_defects: 剩余缺陷
        rounds: 已用轮数
        error_class: 故障分类（distill_no_converge 或 grader_all_down）

    Returns:
        dict: 含 converged=False 和签名信息
    """
    result = record_failure(
        failures_path=failures_path,
        error_class=error_class,
        node="repair_loop",
        loop="loop1",
    )

    return {
        "converged": False,
        "rounds": rounds,
        "final_text": final_text,
        "final_defects": remaining_defects,
        "signature_written": True,
        "failure_signature": result["signature"],
        "escalated": result["escalated"],
    }
