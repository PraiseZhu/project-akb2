"""R1: SG-command-recheck 收窄 —— 内联反引号里的「散文/引用」不再误判 fabrication。

满仓 #0/#2/#8/#10/#17 (concept 页) 死在: `verify_commands_in_raw` 把每个内联反引号
片段都要求原文逐字出现, 把**整条包进反引号的编号散文** (`1. Pick the next`) 和
**repo slug** (`forrestchang/repo`) 误判成 "编造命令"。

修法 (denylist, 不破 SC-4): 只排除两种明确的散文/引用形态; 其余技术字面量
(config / code / 任意工具命令) 仍全文回查。本测试锁两个方向:
1. 编号散文 / repo slug 不再判 fabrication;
2. SC-4 不破 —— config (`trigger={...}`)、任意命令 (`deploy-preview --target`)、
   行号前缀外的真字面量、危险命令、fenced ```bash 块 仍全查。
"""

from __future__ import annotations

from src.akb2.quality.source_ops_scanners.raw_verify import (
    _is_prose_or_reference_literal,
    verify_commands_in_raw,
)

# --- 方向一: 散文/引用形态不再误判 fabrication (满仓真实失败构造) ---

def test_numbered_prose_in_backticks_not_flagged():
    """`1. Pick the next task` 整条编号散文在反引号内 —— 不该判编造 (满仓 #0/#17)。"""
    body = "### 操作步骤\n看 `1. Pick the next task` 这一步。"
    raw = "The agent picks tasks from a queue and executes them."
    assert verify_commands_in_raw(body, raw) == []


def test_numbered_bold_prose_not_flagged():
    """`1. **Cold-start** the agent` 编号+加粗散文 —— 不该判编造 (满仓 #8)。"""
    body = "正文引用 `1. **Cold-start** the agent` 概念点。"
    raw = "Cold-starting an agent means initializing it from scratch."
    assert verify_commands_in_raw(body, raw) == []


def test_repo_slug_not_flagged():
    """`forrestchang/agent-loops` 是 repo slug, 不是命令 —— 不该判编造 (满仓 #10)。"""
    body = "参考实现见 `forrestchang/agent-loops` 仓库。"
    raw = "There are many open-source agent loop implementations."
    cmds = [v.command for v in verify_commands_in_raw(body, raw)]
    assert "forrestchang/agent-loops" not in cmds


# --- 方向二: SC-4 不破 —— 技术字面量仍全文回查 ---

def test_numbered_LINE_prefix_with_real_config_still_rechecked():
    """关键边界: 行号前缀在反引号**外**, 反引号里是真 config → 仍回查 (区分散文 vs 步骤命令)。"""
    body = "1. 配置触发器 `trigger={type:'schedule'}`"
    raw = "raw 里没有这个配置\n"
    cmds = [v.command for v in verify_commands_in_raw(body, raw)]
    assert "trigger={type:'schedule'}" in cmds  # SC-4: config 缺原文仍判编造


def test_fabricated_command_still_flagged():
    """`pip install evil-pkg` 真命令且不在 raw → 仍判编造 (SC-4 不破)。"""
    body = "运行 `pip install evil-pkg` 安装。"
    raw = "Install the package with your package manager."
    cmds = [v.command for v in verify_commands_in_raw(body, raw)]
    assert "pip install evil-pkg" in cmds


def test_arbitrary_tool_command_still_flagged():
    """非标准工具命令 `deploy-preview --target staging` 不在 raw → 仍判编造 (denylist 不漏)。"""
    body = "部署: `deploy-preview --target staging`"
    raw = "raw 里没有 deploy-preview 命令\n"
    cmds = [v.command for v in verify_commands_in_raw(body, raw)]
    assert "deploy-preview --target staging" in cmds


def test_real_command_present_in_raw_not_flagged():
    """`npm run build` 在 raw 中存在 → 不判编造 (真命令正常放行)。"""
    body = "执行 `npm run build` 打包。"
    raw = "To build the project, run npm run build in the root."
    assert verify_commands_in_raw(body, raw) == []


# --- 方向三: 危险命令仍抓 + fenced 块仍全查 (护栏不破) ---

def test_dangerous_command_still_flagged_even_if_in_raw():
    """`rm -rf /` 危险命令 —— 即使在 raw 也判违规 (双重拦截不破)。"""
    body = "千万别 `rm -rf /`。"
    raw = "Never run rm -rf / on your machine."
    reasons = [v.reason for v in verify_commands_in_raw(body, raw)]
    assert any("危险" in r for r in reasons)


def test_dangerous_inside_excluded_prose_still_flagged():
    """即使是被排除的形态, 危险命令仍被拦 (危险黑名单对全部生效)。"""
    body = "步骤 `1. rm -rf / --no-preserve-root` 千万别做。"
    reasons = [v.reason for v in verify_commands_in_raw(body, "irrelevant raw")]
    assert any("危险" in r for r in reasons)


def test_fenced_shell_block_still_fully_rechecked():
    """fenced ```bash 块内的编造命令仍全查 —— 不重开 fenced 绕过。"""
    body = "```bash\nfrobnicate --all-the-things\n```"
    raw = "This document does not contain that command."
    cmds = [v.command for v in verify_commands_in_raw(body, raw)]
    assert "frobnicate --all-the-things" in cmds


# --- 谓词单测 (散文/引用判定边界) ---

def test_predicate_boundaries():
    # 散文 / 引用 → True (排除回查)
    assert _is_prose_or_reference_literal("1. Pick the next task") is True
    assert _is_prose_or_reference_literal("2) Choose a backend") is True
    assert _is_prose_or_reference_literal("forrestchang/agent-loops") is True
    # 技术字面量 → False (仍回查)
    assert _is_prose_or_reference_literal("trigger={type:'schedule'}") is False
    assert _is_prose_or_reference_literal("deploy-preview --target staging") is False
    assert _is_prose_or_reference_literal("pip install x") is False
    assert _is_prose_or_reference_literal("npm run build") is False
    assert _is_prose_or_reference_literal("") is False
