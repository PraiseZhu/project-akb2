"""How 回归测试 — 三类语料防退化（step-8）。

验证 How 链路在三类典型语料上的行为不退化：

1. test-fresh（有操作→捞全）：scan_all(test-fresh raw) 三类 inventory 非空，
   关键操作（initialize/tools/list/tools/call）被捞全，不漏。
2. 纯观点博客（无 How→fail-closed）：scan_all(pure-opinion raw) 的 source_operation
   为空；候选页若硬造 How 编造命令 → run_candidate_self_gate 报 SG-command-recheck
   fail-closed Defect（不硬造 How）。
3. 陷阱样本（枚举带偏→抽核心不被带偏）：scan_all(trap-sample raw) 捞全所有操作
   （含 15 条次要 + 3 条核心 mcp-server），核心命令藏末尾仍被捞全，不被次要带偏；
   must_cover 三类全命中。

SC-8.1 语料就位: 三类语料文件存在于 tests/quality/fixtures/regression/。
SC-8.2 三类行为: 有操作→捞全 / 无 How→fail-closed / 枚举带偏→抽核心。

不改任何 src 实现，只测既有接口（scan_all / run_candidate_self_gate）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from akb2.quality.checks_structural import run_candidate_self_gate
from akb2.quality.source_ops_scanners import scan_all

REGRESSION_DIR = Path(__file__).parent / "fixtures" / "regression"

# 三类语料文件名（SC-8.1 路径断言用）
CORPUS_FILES = (
    "test-fresh--raw.md",
    "pure-opinion--raw.md",
    "trap-sample--raw.md",
    "command-dense-howto--raw.md",
)

EXPECTED_FIXTURE_HASHES = {
    Path("tests/quality/fixtures/test-fresh-golden-ops.json"):
        "53d65299f1da49d6ec2c17aca647c0857853b4bad09b26c8eb50b2b9c8fae2a0",
    Path("tests/quality/fixtures/regression/trap-sample--raw.md"):
        "00642c04002a0b2036b67e44e973b88e1e774082a9d212a87c8824473d2c1e3b",
    Path("tests/quality/fixtures/regression/test-fresh--raw.md"):
        "d0d28eb642e407826fd5288d1b2b8d67233ff86cfb06eee55596d98a5f5983a8",
}

TRAP_MINOR_COMMANDS = [
    "git clone https://github.com/example/repo.git",
    "cd repo",
    "npm install",
    "npm run build",
    "ls -la",
    "cat package.json",
    "echo $PATH",
    "mkdir -p ~/.config/mcp",
    "cp config.json ~/.config/mcp/",
    "chmod 600 ~/.config/mcp/config.json",
    "git status",
    "git log --oneline -5",
    "curl -s localhost:8080/health",
    "docker ps",
    "docker images",
]

TRAP_CORE_COMMANDS = [
    "mcp-server init --config ~/.config/mcp/config.json",
    "mcp-server tools list",
    "mcp-server tools call search",
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _load(name: str) -> str:
    return (REGRESSION_DIR / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def test_fresh_raw() -> str:
    return _load("test-fresh--raw.md")


@pytest.fixture(scope="module")
def pure_opinion_raw() -> str:
    return _load("pure-opinion--raw.md")


@pytest.fixture(scope="module")
def trap_sample_raw() -> str:
    return _load("trap-sample--raw.md")


@pytest.fixture(scope="module")
def command_dense_howto_raw() -> str:
    return _load("command-dense-howto--raw.md")


# ---------------------------------------------------------------------------
# SC-8.1: 语料就位
# ---------------------------------------------------------------------------


class TestCorpusFilesPresent:
    """三类语料文件必须真实存在于 regression/ 目录。"""

    @pytest.mark.parametrize("filename", CORPUS_FILES)
    def test_corpus_file_exists(self, filename: str) -> None:
        path = REGRESSION_DIR / filename
        assert path.is_file(), f"语料文件缺失: {path}"
        assert path.stat().st_size > 0, f"语料文件为空: {path}"

    @pytest.mark.parametrize("relative_path,expected_hash", EXPECTED_FIXTURE_HASHES.items())
    def test_fixture_hashes_match_baseline(
        self, relative_path: Path, expected_hash: str
    ) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        path = repo_root / relative_path
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == expected_hash, f"{relative_path} hash drifted: {digest}"


# ---------------------------------------------------------------------------
# 1. test-fresh（有操作→捞全）
# ---------------------------------------------------------------------------


class TestFreshOperationsFullyCaught:
    """有操作的语料 → scan_all 捞全，不漏关键操作。"""

    def test_three_categories_non_empty(self, test_fresh_raw: str) -> None:
        """scan_all(test-fresh) 三类 inventory 均非空。"""
        items = scan_all(test_fresh_raw)
        kinds = {i.kind for i in items}
        assert "source_operation" in kinds, "test-fresh 应捞到 source_operation"
        assert "source_boundary" in kinds, "test-fresh 应捞到 source_boundary"
        assert "source_conclusion" in kinds, "test-fresh 应捞到 source_conclusion"

    @pytest.mark.parametrize(
        "command",
        ["initialize", "tools/list", "tools/call", "resources/list", "resources/read"],
    )
    def test_key_mcp_operations_caught(
        self, test_fresh_raw: str, command: str
    ) -> None:
        """关键 MCP 协议命令逐字被捞进 source_operation。"""
        items = scan_all(test_fresh_raw)
        ops = {i.literal for i in items if i.kind == "source_operation"}
        assert command in ops, f"关键操作未被捞全: {command!r}"

    def test_operations_fully_caught_not_dropped(self, test_fresh_raw: str) -> None:
        """test-fresh 至少捞到 10 条 operation，证明批量捞全而非只捞首条。"""
        items = scan_all(test_fresh_raw)
        ops = [i for i in items if i.kind == "source_operation"]
        assert len(ops) >= 10, f"操作捞取不足，期望 >=10，实际 {len(ops)}: {ops}"

    def test_span_traceability(self, test_fresh_raw: str) -> None:
        """每个 EvidenceItem 的 raw[span.start:span.end] 包含其 literal（可追溯）。"""
        items = scan_all(test_fresh_raw)
        for item in items:
            covered = test_fresh_raw[item.raw_anchor_span.start : item.raw_anchor_span.end]
            assert item.literal in covered, (
                f"span 不可追溯: literal={item.literal!r} covered={covered!r}"
            )


class TestCommandDenseHowToCorpus:
    """SC-9 正向语料：命令充足时有足够 source_operation 可供 compose 使用。"""

    def test_pip_git_python_commands_are_caught(
        self, command_dense_howto_raw: str
    ) -> None:
        items = scan_all(command_dense_howto_raw)
        ops = [i.literal for i in items if i.kind == "source_operation"]
        expected = [
            "git clone https://github.com/example/ai-agent-eval-loop.git",
            "python -m pip install --upgrade pip",
            "python -m pip install -e \".[dev]\"",
            "python -m pytest tests -q",
            "python -m ruff check src tests",
            "python -m agent_eval run --scenario repair-loop --max-rounds 3",
        ]
        for command in expected:
            assert command in ops, f"命令密集 how-to 语料漏捞: {command!r}; ops={ops}"
        assert len(ops) >= 8


# ---------------------------------------------------------------------------
# 2. 纯观点博客（无 How → fail-closed，不硬造 How）
# ---------------------------------------------------------------------------


class TestPureOpinionNoHowFailClosed:
    """无操作的纯观点语料 → scan_all 返回空 source_operation；
    候选页若硬造 How 编造命令 → run_candidate_self_gate fail-closed。"""

    def test_scan_all_returns_empty_source_operation(
        self, pure_opinion_raw: str
    ) -> None:
        """纯观点博客无 CLI/代码/编号步骤 → source_operation 为空（无 How 证据）。"""
        items = scan_all(pure_opinion_raw)
        ops = [i for i in items if i.kind == "source_operation"]
        assert ops == [], (
            f"纯观点博客不应有 source_operation，却捞到 {len(ops)} 条: {ops}"
        )

    def test_scan_all_returns_empty_all_categories(
        self, pure_opinion_raw: str
    ) -> None:
        """纯观点博客避开所有边界/结论触发词 → 三类全空（确实无 How 证据）。"""
        items = scan_all(pure_opinion_raw)
        assert items == [], (
            f"纯观点博客 scan_all 应为空，实际 {len(items)} 条: "
            f"{[(i.kind, i.literal[:30]) for i in items]}"
        )

    def test_fabricated_how_command_fails_closed(
        self, pure_opinion_raw: str
    ) -> None:
        """候选页硬造一条 raw 里不存在的 backtick 命令 → SG-command-recheck fail-closed。

        纯观点 raw 无任何操作，候选页的 `mcp-server init` 是编造的；
        run_candidate_self_gate 必须挡下（不硬造 How）。
        """
        candidate = _candidate_with_fabricated_how()
        defects = run_candidate_self_gate(
            candidate, raw_content=pure_opinion_raw
        )
        # 必须有 fail-closed 缺陷（编造命令 / must_cover 缺失 / how 密度不足之一）
        fail_closed_dims = {
            "SG-command-recheck",
            "SG-must-cover",
            "SG-how-density",
            "SG-how-executability",
        }
        hit_dims = {d.dimension for d in defects} & fail_closed_dims
        assert hit_dims, (
            "纯观点语料硬造 How 未被 fail-closed 挡下，缺陷维度: "
            f"{sorted({d.dimension for d in defects})}"
        )

    def test_command_recheck_flags_fabricated_command(
        self, pure_opinion_raw: str
    ) -> None:
        """code-direct 编造命令必须被 SG-command-recheck Layer 1 精确挡下。

        候选页 How 步骤带 ``原文操作（must_cover 代码直出）`` 信号（声称命令是
        scanner 从 raw 扫出的字面量），但 pure-opinion raw 无任何操作命令；
        SG-command-recheck Layer 1 回查 raw 发现命令不存在 → Violation → fail-closed。
        """
        candidate = _candidate_with_fabricated_how()
        defects = run_candidate_self_gate(
            candidate, raw_content=pure_opinion_raw
        )
        recheck = [d for d in defects if d.dimension == "SG-command-recheck"]
        assert recheck, (
            "编造命令未被 SG-command-recheck 挡下，全部缺陷: "
            f"{[(d.dimension, d.defect) for d in defects]}"
        )
        assert any("mcp-server init" in d.defect for d in recheck), (
            f"SG-command-recheck 未点名编造命令 mcp-server init: "
            f"{[d.defect for d in recheck]}"
        )
        # 三条编造命令都应被挡下（不只挡第一条）
        assert len(recheck) >= 3, (
            f"SG-command-recheck 应挡下全部 3 条编造命令，实际 {len(recheck)}: "
            f"{[d.defect for d in recheck]}"
        )


# ---------------------------------------------------------------------------
# 3. 陷阱样本（枚举带偏→抽核心不被带偏）
# ---------------------------------------------------------------------------


class TestTrapSampleCoreNotDropped:
    """大量次要操作 + 核心藏末尾 → scan_all 抽核心不被次要带偏。"""

    def test_core_operations_caught_not_dropped(
        self, trap_sample_raw: str
    ) -> None:
        """核心 mcp-server 三条命令藏末尾仍被捞全，不被前面 15 条次要带偏。"""
        items = scan_all(trap_sample_raw)
        ops = [i.literal for i in items if i.kind == "source_operation"]
        assert any("mcp-server init" in o for o in ops), (
            f"核心 mcp-server init 被次要带偏漏掉，ops={ops}"
        )
        assert any("mcp-server tools list" in o for o in ops), (
            f"核心 mcp-server tools list 被漏掉，ops={ops}"
        )
        assert any("mcp-server tools call" in o for o in ops), (
            f"核心 mcp-server tools call 被漏掉，ops={ops}"
        )

    def test_minor_operations_also_caught(self, trap_sample_raw: str) -> None:
        """次要操作也被捞全（不因只盯核心而漏次要）。"""
        items = scan_all(trap_sample_raw)
        ops = [i.literal for i in items if i.kind == "source_operation"]
        for command in TRAP_MINOR_COMMANDS:
            assert command in ops, f"次要操作漏掉: {command!r}; ops={ops}"

    def test_core_operations_exact_commands_caught(self, trap_sample_raw: str) -> None:
        """3 条核心命令 exact 命中，不只靠子串 must-contain。"""
        items = scan_all(trap_sample_raw)
        ops = [i.literal for i in items if i.kind == "source_operation"]
        for command in TRAP_CORE_COMMANDS:
            assert command in ops, f"核心操作 exact 漏掉: {command!r}; ops={ops}"

    def test_operations_count_full_not_truncated(
        self, trap_sample_raw: str
    ) -> None:
        """15 条次要 + 3 条核心 = 18 条 operation 全捞，证明未因数量多而截断。"""
        items = scan_all(trap_sample_raw)
        ops = [i for i in items if i.kind == "source_operation"]
        assert len(ops) >= 18, (
            f"操作捞取被截断，期望 >=18（15 次要+3 核心），实际 {len(ops)}: {ops}"
        )

    def test_must_cover_three_categories_all_hit(
        self, trap_sample_raw: str
    ) -> None:
        """must_cover 三类全命中：核心 operation + 边界 + 结论都不因操作多而漏。"""
        items = scan_all(trap_sample_raw)
        kinds = {i.kind for i in items}
        assert "source_operation" in kinds, "陷阱样本漏 source_operation"
        assert "source_boundary" in kinds, "陷阱样本漏 source_boundary"
        assert "source_conclusion" in kinds, "陷阱样本漏 source_conclusion"

    def test_boundary_and_conclusion_caught(self, trap_sample_raw: str) -> None:
        """边界句与结论句被捞全（核心结论不因操作多而被漏）。"""
        items = scan_all(trap_sample_raw)
        bds = [i.literal for i in items if i.kind == "source_boundary"]
        cls = [i.literal for i in items if i.kind == "source_conclusion"]
        assert any("仅限生产环境" in b for b in bds), f"边界句漏掉: {bds}"
        assert any("因此" in c for c in cls), f"核心结论句漏掉: {cls}"

    def test_core_appears_after_minor_in_span_order(
        self, trap_sample_raw: str
    ) -> None:
        """核心命令的 span 在次要命令之后（证明核心确实藏末尾，未被带偏到漏掉）。"""
        items = scan_all(trap_sample_raw)
        ops = [i for i in items if i.kind == "source_operation"]
        core_idx = next(
            (i for i, o in enumerate(ops) if "mcp-server init" in o.literal), None
        )
        first_minor_idx = next(
            (i for i, o in enumerate(ops) if "git clone" in o.literal), None
        )
        assert core_idx is not None and first_minor_idx is not None
        assert core_idx > first_minor_idx, (
            f"核心应在次要之后（藏末尾），实际 core_idx={core_idx} "
            f"minor_idx={first_minor_idx}"
        )


# ---------------------------------------------------------------------------
# Helper: 构造一个硬造 How 的候选页（用于 fail-closed 断言）
# ---------------------------------------------------------------------------


def _candidate_with_fabricated_how() -> str:
    """构造一个含 code-direct 编造命令的候选页骨架。

    候选页 ### 操作步骤 写了 3 条带 ``原文操作（must_cover 代码直出）`` 信号
    的 backtick 命令（`mcp-server init` 等），声称是 scanner 从 raw 扫出的
    字面量；但与之配对的 raw（pure-opinion）里根本没有任何操作命令。
    run_candidate_self_gate 的 SG-command-recheck Layer 1 回查 raw 发现命令
    不存在 → Violation → fail-closed（不硬造 How）。
    """
    return """---
status: candidate
gate_decision: null
source_refs:
  - raw/pure-opinion--raw.md
gate_reason: 待评审
future_questions:
  - Q1?
  - Q2?
related_concepts:
  - concept-a
  - concept-b
key_findings:
  - "[raw §A] (source_grounded) finding"
artifact_type: wiki
pipeline_version: v1
---

## TLDR

关于 AI 可靠性的观点 [raw §A]。

## 关键发现

1. finding [raw §A]

## 入选理由

理由。

## 证据地图

| 论点 | 锚点 | 证据强度 | 未覆盖 |
|------|------|---------|--------|
| A | §A | strong | - |

## 核心论点

1. 关于 AI 可靠性的观点 [raw §A]

## 论证与解释

### 操作步骤

1. [raw §A] (source_grounded) 原文操作（must_cover 代码直出，scanner=cli_commands） `mcp-server init --config ~/.config/mcp/config.json`
2. [raw §A] (source_grounded) 原文操作（must_cover 代码直出，scanner=cli_commands） `mcp-server tools list`
3. [raw §A] (source_grounded) 原文操作（must_cover 代码直出，scanner=cli_commands） `mcp-server tools call search`

## 适用边界

- [raw §A] (source_grounded) 仅限生产环境使用。

## 图谱关系

| target | relation | strength | evidence |
|--------|----------|----------|----------|
| concept-a | related | weak | [raw §A] (source_grounded) x |

## Future Questions

1. Q1?
2. Q2?

## 维护记录

- 2026-06-23: 初始创建（待评审）
"""
