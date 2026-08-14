"""SKILL.md submit 命令模板提取器（sc-skill-def change ④ 契约面）。

「收割昨天」SKILL.md 中必须**恰有一段**带唯一稳定标记的 submit 命令模板：

    <!-- akb2-harvest:submit-template -->
    python3 -m akb2.harvest --harvest-json {{harvest_json}} --harvest-run-id {{harvest_run_id}}
    <!-- /akb2-harvest:submit-template -->

约束（GPT 质询 rev24-ch7 定死，防「grep / 模板测试 / smoke 四处同值全绿、
真实试投必挂」的假绿）：
- 占位符语法固定为 `{{harvest_json}}` 与 `{{harvest_run_id}}`。
- 该模板即手动执行章节引用的同一段，文中他处不得另写第二条 submit 命令。
- **提取器对零个或多个标记块一律失败**（不取第一个、不猜）。
"""

from __future__ import annotations

# 唯一稳定标记（注释体逐字固定）
OPEN_MARK = "<!-- akb2-harvest:submit-template -->"
CLOSE_MARK = "<!-- /akb2-harvest:submit-template -->"

# 占位符语法固定（change 条款，逐字）
PLACEHOLDER_JSON = "{{harvest_json}}"
PLACEHOLDER_RUN_ID = "{{harvest_run_id}}"


class TemplateExtractionError(ValueError):
    """SKILL.md 模板结构非法：零个/多个标记块、块不成对、缺占位符。"""


def extract_submit_template(skill_md: str) -> str:
    """从 SKILL.md 文本提取唯一 submit 命令模板。

    Args:
        skill_md: SKILL.md 全文。

    Returns:
        块内命令文本（不含注释行，已 strip）。

    Raises:
        TemplateExtractionError: 标记块数量不是恰好一个、块不成对、
            或块内缺固定占位符。
    """
    open_count = skill_md.count(OPEN_MARK)
    close_count = skill_md.count(CLOSE_MARK)

    if open_count != 1 or close_count != 1:
        raise TemplateExtractionError(
            f"SKILL.md submit 模板必须是恰一对标记注释："
            f"开始注释 {open_count} 个、结束注释 {close_count} 个"
            f"（期望各 1 个，零个/多个一律拒绝）"
        )

    open_idx = skill_md.index(OPEN_MARK) + len(OPEN_MARK)
    close_idx = skill_md.index(CLOSE_MARK)
    if close_idx <= open_idx:
        raise TemplateExtractionError(
            "SKILL.md submit 模板结构非法：结束注释不在开始注释之后"
        )

    body = skill_md[open_idx:close_idx].strip()
    if not body:
        raise TemplateExtractionError("SKILL.md submit 模板为空")

    if PLACEHOLDER_JSON not in body or PLACEHOLDER_RUN_ID not in body:
        raise TemplateExtractionError(
            f"SKILL.md submit 模板必须含占位符 {PLACEHOLDER_JSON} 与"
            f" {PLACEHOLDER_RUN_ID}（占位符语法固定，不得改写）"
        )
    return body
