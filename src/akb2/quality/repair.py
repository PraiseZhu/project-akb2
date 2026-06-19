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
import re
from datetime import UTC, datetime
from typing import Any

from src.akb2.loops.signatures import record_failure
from src.akb2.quality.checks_structural import (
    Defect,
    _extract_body_text,
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

REPAIR_INSTRUCTION_TEMPLATE = """\
你是知识库内容编辑。对候选页定点修复(根因③:不整页重写,只改点名节)。

## 修复规则(必须严格遵守)

1. **只修改被点名的节**——下方"待修复节"的 location 字段指向的节标题。
2. **未点名的节你碰不到**——由代码拼回,逐字节不变。
3. 每个缺陷的 suggestion 给出修复方向,参考**黄金样张**对齐质量。
4. **只输出严格 JSON**,格式如下:
   `{{"节标题1": "该节新内容(从 ## 标题行起)", ...}}`
   - 只含被点名的节
   - 每节新内容从 `## 节标题` 开始(含标题行)
   - **不要输出完整候选页,不要任何解释**

## 原文 raw(锚点映射:论点 ↔ 原文 §X 段;加 [raw §X] 时按此映射,不可臆造)

{raw_content}

## 黄金样张(质量标杆)

{golden_excerpt}

## 待修复节(节标题 → 缺陷 → 建议)

{defects_text}

## 候选页原文(供参考上下文,不要整体重写)

{candidate_text}

只输出 JSON。"""


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


def _find_sections_h2_h3(text: str) -> list[tuple[int, str]]:
    """根因②(2026-06-18 修):repair 定位不只认 H2,也认 H3。

    checks_structural._find_section_boundaries 只认 H2(服务 A 层 10 节模板);
    repair 的缺陷常指向 H3 子节,故本模块独立实现 H2+H3 识别——
    不改 checks_structural(避免破坏 A 层 H2 边界契约)。

    Returns:
        list[(level, title)] — level in (2, 3),不含 ## 前缀。
    """
    sections: list[tuple[int, str]] = []
    for line in text.split("\n"):
        stripped = line.strip()
        m = re.match(r"^(#{2,3})\s+(.+)$", stripped)
        if m:
            sections.append((len(m.group(1)), m.group(2).strip()))
    return sections


def _normalize_loc(s: str) -> str:
    """根因①(2026-06-18 修):location/标题归一化用于模糊匹配。

    grader 给的 location 是自由文本(如"操作步骤1-9" / "核心论点第2条"),
    与节标题(如"核心论点")精确子串匹配命中率低。归一化(去标点/空格/小写)
    后双向子串匹配,提升定位命中率。
    """
    return re.sub(r"[\s\W_]+", "", str(s)).lower()


def _apply_section_patches(
    body: str, patches: Any, allowed_titles: set[str] | None = None
) -> str:
    """根因③:定点爬坡——只替换被点名节,其余逐字节不变。

    LLM 不再重写整页(旧机制下整页重写常动未点名节标点/空格→verify 违规→整轮丢,
    即 addy 卡 grade 7→10→12 的根因);改为只返回 `{节标题: 该节新内容}` JSON,
    本函数拼回 body,从机制上保证未点名节 repair 碰不到。

    问题5(Codex):传 allowed_titles(named)时,只 patch allowed ∩ patch keys,
    丢弃 LLM 返回的额外/未点名 key(从机制上杜绝"改未点名节")。

    Args:
        body: 候选页正文(frontmatter 之后)
        patches: dict {节标题: 新内容} 或 JSON 字符串;非 dict/解析失败 → 原样返回
        allowed_titles: 允许 patch 的节标题集合(named);None 时不过滤(全部 patch)
    Returns:
        拼回后的 body(点名节替换,其余原样)
    """
    if isinstance(patches, str):
        try:
            patches = json.loads(patches)
        except json.JSONDecodeError:
            return body  # 解析失败不拼回(上层 verify/记 signature 处理)
    if not isinstance(patches, dict):
        return body

    # 问题3(Codex):区分 None(不过滤) vs 空集合(零 patch)——
    # 空定位(named_sections=set())时禁止应用任意 patch(原 if allowed_titles 把空当 None)
    allowed_norm = (
        {_normalize_loc(t) for t in allowed_titles}
        if allowed_titles is not None
        else None
    )
    norm_to_new = {
        _normalize_loc(t): new
        for t, new in patches.items()
        if allowed_norm is None or _normalize_loc(t) in allowed_norm
    }
    lines = body.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        m = re.match(r"^(#{2,3})\s+(.+)$", stripped)
        title_norm = _normalize_loc(m.group(2)) if m else ""
        if m and title_norm in norm_to_new:
            # 替换该节:用 patch 新内容(含 ## 标题行)
            out.append(str(norm_to_new[title_norm]).rstrip())
            # 跳过原节体(到下一个同级或更高级标题前)
            level = len(m.group(1))
            i += 1
            while i < len(lines):
                nxt = lines[i].strip()
                nm = re.match(r"^(#{2,3})\s+", nxt)
                if nm and len(nm.group(1)) <= level:
                    break  # 同级或上级标题 → 本节结束
                i += 1
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


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

    all_sections = (
        {t for _, t in _find_sections_h2_h3(orig_body)}
        | {t for _, t in _find_sections_h2_h3(rep_body)}
    )

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
    golden_sample_text: str | None = None,
    raw_text: str | None = None,
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
        # 根因①②:定位认 H2+H3 + location 归一化匹配
        # 标题归一预算一次(消 D×T 重算);body 本轮不变,apply 段复用
        body = _extract_body_text(current_text)
        title_norms = [(t, _normalize_loc(t)) for _, t in _find_sections_h2_h3(body)]
        named_sections: set[str] = set()
        for d in current_defects:
            loc_norm = _normalize_loc(d.location)
            for title, t_norm in title_norms:
                if t_norm and (t_norm in loc_norm or loc_norm in t_norm):
                    named_sections.add(title)
                    break

        # 构建修复指令(根因③:喂黄金样张作标杆)
        instruction_text = _build_repair_instruction(current_defects)
        prompt = REPAIR_INSTRUCTION_TEMPLATE.format(
            defects_text=instruction_text,
            candidate_text=current_text,
            golden_excerpt=golden_sample_text or "(本次未提供黄金样张;按候选页自身质量锚定)",
            raw_content=raw_text or "(本次未提供原文;[raw §X] 锚点按候选页已有锚点推断)",
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

            # 根因③:repair 返回 JSON → 定点拼回(apply 限 named,未命名节逐字节不变)
            patches = response.content
            new_body = _apply_section_patches(body, patches, allowed_titles=named_sections)
            fm_match = re.match(r"^---\s*\n.*?\n---\s*\n", current_text, re.DOTALL)
            repaired_text = (fm_match.group(0) + new_body) if fm_match else new_body
        except (RouterConfigError, RuntimeError):
            # 根因④(2026-06-18 修):路由失败/超时不再静默吞(违宪法 P4 失败可见)
            # 记 signature 留痕后 continue 重试(瞬时故障重试有意义);3 轮全失败仍走 fail_closed
            record_failure(
                failures_path=failures_path,
                error_class="repair_route_failed",
                node="repair_loop",
                loop="loop1",
            )
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
