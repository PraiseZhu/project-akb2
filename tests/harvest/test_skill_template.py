"""SKILL.md submit 命令模板提取器测试（sc-skill-def expect ①③④⑥ 机器面）。

标记块语法（sc-skill-def change 定死）:
    <!-- akb2-harvest:submit-template -->
    <命令文本, 占位符固定为 {{harvest_json}} 与 {{harvest_run_id}}>
    <!-- /akb2-harvest:submit-template -->

提取器对零个或多个标记块一律失败（不取第一个、不猜）。
"""

from __future__ import annotations

import pytest

from akb2.harvest.template import (
    CLOSE_MARK,
    OPEN_MARK,
    TemplateExtractionError,
    extract_submit_template,
)

# ---------------------------------------------------------------------------
# 样例语料
# ---------------------------------------------------------------------------

_SINGLE_TEMPLATE_MD = (
    "---\n"
    "name: akb2-harvest\n"
    "description: 收割昨天\n"
    "---\n\n"
    "## 五步流程\n\n"
    "运行下方模板命令：\n\n"
    f"{OPEN_MARK}\n"
    "python3 -m akb2.harvest --harvest-json {{harvest_json}}"
    " --harvest-run-id {{harvest_run_id}}\n"
    f"{CLOSE_MARK}\n\n"
    "## 手动执行\n\n"
    "复制上方模板命令逐字执行。\n"
)

_TWO_TEMPLATES_MD = _SINGLE_TEMPLATE_MD + (
    f"\n\n{OPEN_MARK}\n"
    "python3 -m akb2.harvest --harvest-json {{harvest_json}}"
    " --harvest-run-id {{harvest_run_id}}\n"
    f"{CLOSE_MARK}\n"
)


# ---------------------------------------------------------------------------
# 红集：零个 / 多个标记块一律失败
# ---------------------------------------------------------------------------


def test_zero_blocks_raises():
    """零个标记块 → TemplateExtractionError（不得返回空命令凑合）。"""
    md = "## 五步流程\n运行: python3 -m akb2.harvest --harvest-json x\n"
    with pytest.raises(TemplateExtractionError):
        extract_submit_template(md)


def test_two_blocks_raises():
    """多个标记块 → TemplateExtractionError（不得取第一个）。"""
    with pytest.raises(TemplateExtractionError):
        extract_submit_template(_TWO_TEMPLATES_MD)


def test_open_without_close_raises():
    """只有开始注释没有结束注释 → 失败（不成对）。"""
    md = "# t\n\n" + OPEN_MARK + "\npython3 -m akb2.harvest --harvest-json x\n"
    with pytest.raises(TemplateExtractionError):
        extract_submit_template(md)


def test_close_without_open_raises():
    """只有结束注释没有开始注释 → 失败（不成对）。"""
    md = "# t\n\n" + CLOSE_MARK + "\n"
    with pytest.raises(TemplateExtractionError):
        extract_submit_template(md)


def test_unclosed_between_blocks_raises():
    """开始注释后跟第二个开始注释 → 结构非法，失败。"""
    md = OPEN_MARK + "\n" + OPEN_MARK + "\npython3 -m akb2.harvest x\n" + CLOSE_MARK + "\n"
    with pytest.raises(TemplateExtractionError):
        extract_submit_template(md)


# ---------------------------------------------------------------------------
# 绿集：恰好一个标记块 → 返回块内命令文本
# ---------------------------------------------------------------------------


def test_single_block_returns_command():
    """恰好一个标记块 → 返回命令文本（不含注释行）。"""
    command = extract_submit_template(_SINGLE_TEMPLATE_MD)
    assert command.startswith("python3 -m akb2.harvest")
    assert "--harvest-json" in command
    assert "--harvest-run-id" in command
    assert "{{harvest_json}}" in command
    assert "{{harvest_run_id}}" in command


def test_extracted_command_matches_manual_copy_path():
    """手动复制路径与提取器是同一段（逐字相等断言）。

    手动执行章节不另写第二条命令——全文 `-m akb2.harvest` 恰好 1 次
    （在模板块内），因此提取结果即唯一命令。
    """
    assert _SINGLE_TEMPLATE_MD.count("-m akb2.harvest") == 1
    command = extract_submit_template(_SINGLE_TEMPLATE_MD)
    expected = (
        "python3 -m akb2.harvest --harvest-json {{harvest_json}}"
        " --harvest-run-id {{harvest_run_id}}"
    )
    assert command == expected


def test_extract_ignores_surrounding_text():
    """块外文本（含无关命令字样的说明）不影响提取。"""
    md = (
        "\n# 收割昨天\n\n"
        "本 skill 通过 get_chat_history 拉昨日会话。\n\n"
        + OPEN_MARK
        + "\n"
        "python3 -m akb2.harvest --harvest-json {{harvest_json}}"
        " --harvest-run-id {{harvest_run_id}}\n"
        + CLOSE_MARK
        + "\n\n（说明文字，不含命令）\n"
    )
    command = extract_submit_template(md)
    assert "--harvest-run-id" in command
