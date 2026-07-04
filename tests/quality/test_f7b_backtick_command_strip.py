"""test_f7b_backtick_command_strip.py — F7-B 编造命令剥离被绕过修复测试。

覆盖:
    - ~/；claude/rules/ 形态(probe-p6 memory 死因)在 how_text 里 → 剥反引号
    - 真命令(在 raw 中)→ 保留反引号
    - 空反引号 → 不动
    - 整段 how_text 多条命令混合 → 只剥不在 raw 的
"""

from __future__ import annotations

from src.akb2.quality.distill import _strip_ungrounded_backtick_commands

_RAW = """\
some raw text
make deploy
python -m akb2.quality.runner --once
"""


class TestF7BBacktickCommandStrip:
    def test_fabricated_path_stripped(self):
        """~/；claude/rules/ 不在 raw → 剥反引号(保留文本,SG 不再回查)。"""
        text = "Run `~/；claude/rules/` to configure."
        result = _strip_ungrounded_backtick_commands(text, _RAW)
        assert "`~/；claude/rules/`" not in result
        assert "~/；claude/rules/" in result  # 文本保留,反引号剥

    def test_real_command_preserved(self):
        """make deploy 在 raw → 保留反引号。"""
        text = "Run `make deploy` to build."
        result = _strip_ungrounded_backtick_commands(text, _RAW)
        assert "`make deploy`" in result

    def test_multiple_commands_mixed(self):
        """多条命令混合 → 只剥不在 raw 的。"""
        text = (
            "1. Run `make deploy` to build.\n"
            "2. Use `~/；claude/rules/` to configure.\n"
            "3. Run `python -m akb2.quality.runner --once` to test.\n"
            "4. See `；claude/rules/` for details.\n"
        )
        result = _strip_ungrounded_backtick_commands(text, _RAW)
        assert "`make deploy`" in result  # 在 raw,保留
        assert "`python -m akb2.quality.runner --once`" in result  # 在 raw,保留
        assert "`~/；claude/rules/`" not in result  # 不在 raw,剥
        assert "`；claude/rules/`" not in result  # 不在 raw,剥
        # 文本保留
        assert "~/；claude/rules/" in result
        assert "；claude/rules/" in result

    def test_empty_backtick_not_touched(self):
        """空反引号 → 不动。"""
        text = "Empty `` here."
        result = _strip_ungrounded_backtick_commands(text, _RAW)
        assert "``" in result

    def test_no_raw_text_noop(self):
        """无 raw_text → 不动(fail-closed 不造假)。"""
        text = "Run `make deploy`."
        result = _strip_ungrounded_backtick_commands(text, "")
        assert result == text  # 无 raw 不剥(SG 会 fail-closed)
