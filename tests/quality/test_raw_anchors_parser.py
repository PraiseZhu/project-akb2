"""test_raw_anchors_parser.py — P1a 权威 anchor parser 测试。

覆盖:
    - 4 声明形态(heading/marker/dir-table/dir-rawline) × 下划线 × 多#
    - marker 后缀剥离归一(_CONTENT_START/_CONTENT_END 剥,业务下划线保留)+ 非贪婪消歧
    - [B4] 正文提及/正文表格/正文 [raw §X] 行不算声明
    - 引用 regex 认下划线 + canonicalize 比对
    - 空 raw → 空集(fail-closed)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.akb2.quality.raw_anchors import (
    ANCHOR_VALUE_RE,
    RAW_ANCHOR_REF_RE,
    canonicalize_anchor,
    declared_anchor_positions,
    declared_anchor_set,
    nearest_preceding_anchor,
)


# ---------------------------------------------------------------------------
# 4 声明形态 × 下划线 × 多#
# ---------------------------------------------------------------------------


class TestDeclarationForms:
    def test_heading_form_single_letter_and_multi_hash(self):
        """heading `## §A Overview` / `### §A` / `# §1 Initialize` 都声明。"""
        raw = "## §A Overview\n\n### §A sub\n\n# §1 Initialize"
        assert declared_anchor_set(raw) == {"§A", "§1"}

    def test_heading_form_with_underscore_business_anchor(self):
        """heading `## §I_LIMITATIONS Title` 保留全名。"""
        raw = "## §I_LIMITATIONS Title\n\nbody"
        assert declared_anchor_set(raw) == {"§I_LIMITATIONS"}

    def test_marker_form_content_start_end_stripped(self):
        """raw_archiver marker `§A_CONTENT_START` / `§Z_CONTENT_END` 剥后缀 → §A/§Z。"""
        raw = "§A_CONTENT_START\n\nbody\n\n§Z_CONTENT_END"
        assert declared_anchor_set(raw) == {"§A", "§Z"}

    def test_marker_form_business_underscore_kept(self):
        """raw_archiver marker `§I_LIMITATIONS` / `§B_INTRODUCTION` 保留全名。"""
        raw = "§A_CONTENT_START\n\n§B_INTRODUCTION\n\n§I_LIMITATIONS\n\n§Z_CONTENT_END"
        assert declared_anchor_set(raw) == {
            "§A",
            "§B_INTRODUCTION",
            "§I_LIMITATIONS",
            "§Z",
        }

    def test_directory_block_table_cell_declares(self):
        """目录块内 `| §X |` 表格单元算声明。"""
        raw = "## 锚点目录\n\n| §B | x |\n| §I | y |\n\n---\n\nbody"
        assert declared_anchor_set(raw) == {"§B", "§I"}

    def test_directory_block_rawline_declares(self):
        """目录块内行首 `[raw §X]` 算声明。"""
        raw = "## 锚点目录\n\n[raw §A]\n[raw §I]\n\n---\n\nbody"
        assert declared_anchor_set(raw) == {"§A", "§I"}


# ---------------------------------------------------------------------------
# marker 剥离归一 + 非贪婪消歧
# ---------------------------------------------------------------------------


class TestCanonicalize:
    def test_strip_content_start_end(self):
        assert canonicalize_anchor("§A_CONTENT_START") == "§A"
        assert canonicalize_anchor("§Z_CONTENT_END") == "§Z"

    def test_keep_business_underscore(self):
        assert canonicalize_anchor("§I_LIMITATIONS") == "§I_LIMITATIONS"
        assert canonicalize_anchor("§B_INTRODUCTION") == "§B_INTRODUCTION"

    def test_bare_anchor_unchanged(self):
        assert canonicalize_anchor("§A") == "§A"
        assert canonicalize_anchor("§1") == "§1"

    def test_non_greedy_no_partial_strip(self):
        """`§A_CONTENT_START` 剥完整 `_CONTENT_START` → `§A`,不剥成 `§A_CONTENT`。"""
        assert canonicalize_anchor("§A_CONTENT_START") != "§A_CONTENT"
        assert canonicalize_anchor("§A_CONTENT_START") == "§A"
        # `§_CONTENT_START`(无字母前缀)不剥——剥后 `§` 非法裸锚
        assert canonicalize_anchor("§_CONTENT_START") == "§_CONTENT_START"


# ---------------------------------------------------------------------------
# [B4] 正文提及/正文表格/正文 [raw §X] 行不算声明
# ---------------------------------------------------------------------------


class TestBodyMentionsNotDeclarations:
    def test_inline_mention_not_declared(self):
        """正文散文偶然提到 `§Z` 不进 declared set。"""
        raw = "## §A. 开篇\n\n这段文字只是说不要引用 §Z,因为它不存在。"
        assert declared_anchor_set(raw) == {"§A"}
        assert "§Z" not in declared_anchor_set(raw)

    def test_body_table_not_declared(self):
        """正文表格 `| §Z | meaning |` 不算声明(不在目录块)。"""
        raw = "## §A\n\n| §Z | meaning |\n| §A | real |"
        assert declared_anchor_set(raw) == {"§A"}
        assert "§Z" not in declared_anchor_set(raw)

    def test_body_rawline_not_declared(self):
        """[B4] 正文 `[raw §A] text` 段落行不算声明(只目录块内 [raw §X] 算)。"""
        raw = "[raw §A] Loop engineering is bounded.\n\n[raw §I] A loop is deployable."
        # 无 heading/marker/目录块 → 空集(fail-closed:这些 [raw §X] 是引用,非声明)
        assert declared_anchor_set(raw) == set()

    def test_body_rawline_with_underscore_not_declared(self):
        """正文 `[raw §I_LIMITATIONS] text` 也不算声明。"""
        raw = "[raw §I_LIMITATIONS] some text about limits"
        assert declared_anchor_set(raw) == set()


# ---------------------------------------------------------------------------
# 引用 regex 认下划线 + canonicalize 比对
# ---------------------------------------------------------------------------


class TestReferenceRegex:
    def test_ref_matches_single_letter_and_underscore(self):
        """`[raw §A]` / `[raw §I_LIMITATIONS]` / `[raw §A_CONTENT_START]` 都能匹配。"""
        assert RAW_ANCHOR_REF_RE.findall("text [raw §A] more") == ["§A"]
        assert RAW_ANCHOR_REF_RE.findall("[raw §I_LIMITATIONS] text") == ["§I_LIMITATIONS"]
        assert RAW_ANCHOR_REF_RE.findall("[raw §A_CONTENT_START]") == ["§A_CONTENT_START"]

    def test_ref_canonicalize_matches_declared(self):
        """引用经 canonicalize 后与 declared set 比对:
        - [raw §A] / [raw §A_CONTENT_START] 都匹配声明 §A(剥后缀)
        - [raw §I_LIMITATIONS] 匹配声明 §I_LIMITATIONS(保留)
        - [raw §I] 不匹配声明 §I_LIMITATIONS(fail-closed,宁缺勿假)
        """
        raw = "§A_CONTENT_START\n\n§I_LIMITATIONS\n\n§Z_CONTENT_END"
        declared = declared_anchor_set(raw)
        assert canonicalize_anchor("§A") in declared
        assert canonicalize_anchor("§A_CONTENT_START") in declared
        assert canonicalize_anchor("§I_LIMITATIONS") in declared
        # 单字母 §I 不匹配业务锚 §I_LIMITATIONS(强制精确匹配)
        assert canonicalize_anchor("§I") not in declared

    def test_anchor_value_re_accepts_underscore(self):
        """ANCHOR_VALUE_RE 校验 LLM 产 anchor 字段:单字母 + 业务下划线都合法。"""
        assert ANCHOR_VALUE_RE.match("§A")
        assert ANCHOR_VALUE_RE.match("§I_LIMITATIONS")
        assert ANCHOR_VALUE_RE.match("§A_CONTENT_START")
        assert ANCHOR_VALUE_RE.match("§1")
        assert not ANCHOR_VALUE_RE.match("§a")  # 小写不合法
        assert not ANCHOR_VALUE_RE.match("§A content")  # 含空格
        assert not ANCHOR_VALUE_RE.match("")


# ---------------------------------------------------------------------------
# 空 raw → 空集(fail-closed)
# ---------------------------------------------------------------------------


class TestFailClosed:
    def test_empty_raw_returns_empty_set(self):
        assert declared_anchor_set("") == set()
        assert declared_anchor_set(None) == set()

    def test_nearest_preceding_empty_when_no_decls(self):
        """无声明锚点 → nearest_preceding 返空串(fail-closed,不回落首锚)。"""
        assert nearest_preceding_anchor("", 0) == ""
        assert nearest_preceding_anchor("no anchors here", 5) == ""

    def test_nearest_preceding_no_fallback_before_first_decl(self):
        """span 先于所有声明(frontmatter)→ 返空串(不回落首锚,防错配)。"""
        raw = "prologue text\n\n## §A\n\nbody"
        decls = declared_anchor_positions(raw)
        # span 在 prologue(声明之前)
        assert nearest_preceding_anchor(raw, 3, decls) == ""

    def test_nearest_preceding_returns_preceding(self):
        raw = "§A_CONTENT_START\n\npara A\n\n§B_INTRODUCTION\n\npara B"
        decls = declared_anchor_positions(raw)
        # span 在 para B(§B 之后)
        para_b_pos = raw.find("para B")
        assert nearest_preceding_anchor(raw, para_b_pos, decls) == "§B_INTRODUCTION"
        # span 在 para A(§A 之后,§B 之前)
        para_a_pos = raw.find("para A")
        assert nearest_preceding_anchor(raw, para_a_pos, decls) == "§A"


# ---------------------------------------------------------------------------
# positions 有序 + 多形态混合
# ---------------------------------------------------------------------------


class TestPositionsAndMixed:
    def test_positions_sorted_by_char_offset(self):
        raw = "## §A\n\n§B_INTRODUCTION\n\n| §C |\n\nstuff"
        decls = declared_anchor_positions(raw)
        # 注意 `| §C |` 在正文(无目录块)不算声明,只有 heading §A + marker §B_INTRODUCTION
        anchors_by_pos = [a for _, a in decls]
        assert anchors_by_pos == ["§A", "§B_INTRODUCTION"]
        # 位置升序
        positions = [p for p, _ in decls]
        assert positions == sorted(positions)

    def test_raw_archiver_format_full_declared_set(self):
        """raw_archiver 完整产出形态:TOC(- §X: list) + body markers。"""
        raw = (
            "---\nartifact_type: raw\n---\n\n"
            "## 锚点目录\n\n"
            "- §A: CONTENT_START\n- §B: INTRODUCTION\n- §I: LIMITATIONS\n- §Z: CONTENT_END\n\n"
            "---\n\n"
            "§A_CONTENT_START\n\nfirst para\n\n"
            "§B_INTRODUCTION\n\nsecond para\n\n"
            "§I_LIMITATIONS\n\nlimits para\n\n"
            "§Z_CONTENT_END\n"
        )
        declared = declared_anchor_set(raw)
        # TOC list 条目(- §X:)不算声明,但 body markers 声明全部
        assert declared == {
            "§A",
            "§B_INTRODUCTION",
            "§I_LIMITATIONS",
            "§Z",
        }

    def test_fixture_heading_format(self):
        """fixture heading 形态(command-dense-howto 风格)。"""
        raw = (
            "# Title\n\n"
            "## §A Overview\n\nintro\n\n"
            "## §S Setup\n\nsetup\n\n"
            "## §V Verify\n\nverify\n\n"
            "## §B Boundary\n\nboundary\n\n"
            "## §C Conclusion\n\nconcl"
        )
        assert declared_anchor_set(raw) == {"§A", "§S", "§V", "§B", "§C"}


# ---------------------------------------------------------------------------
# P1a [B3] rg 守护:quality 包内(raw_anchors.py 自身除外)不得散落 §[A-Z0-9 本地 regex
# ---------------------------------------------------------------------------


class TestRgGuard:
    """禁止 quality 包内(raw_anchors.py 除外)再散落 `§[A-Z0-9` 开头的本地 anchor regex。

    历史病灶:5 套 anchor 口径各自定义 `§[A-Z0-9]+` / `§[A-Z0-9_]+` 互相打架。
    P1a 后全链统一到 raw_anchors,消费方一律用 ANCHOR_TOKEN_PAT 等导入常量,
    源码里不得再出现 `§[A-Z0-9` 字面(否则就是又一处本地口径)。
    """

    def test_no_local_anchor_regex_in_quality_pkg(self):
        quality_dir = Path("src/akb2/quality")
        offenders: list[str] = []
        for py in quality_dir.rglob("*.py"):
            if py.name == "raw_anchors.py":
                continue
            text = py.read_text(encoding="utf-8")
            if "§[A-Z0-9" in text:
                offenders.append(str(py))
        assert offenders == [], (
            f"quality 包内发现本地 §[A-Z0-9 anchor regex(应统一走 raw_anchors): {offenders}"
        )


# ---------------------------------------------------------------------------
# P1a 四路等价锁:checks/runner/distill 同输入同 canonical 集合
# ---------------------------------------------------------------------------


_FIXTURE_HEADING = (
    "# Title\n\n## §A Overview\n\nintro\n\n## §S Setup\n\nsetup\n\n"
    "## §I_LIMITATIONS Title\n\nlimits\n\n## §C Conclusion\n\nconcl"
)
_RAW_ARCHIVER = (
    "---\nartifact_type: raw\n---\n\n"
    "## 锚点目录\n\n- §A: CONTENT_START\n- §I: LIMITATIONS\n- §Z: CONTENT_END\n\n"
    "---\n\n"
    "§A_CONTENT_START\n\nfirst para\n\n§I_LIMITATIONS\n\nlimits para\n\n§Z_CONTENT_END\n"
)
_MIXED = (
    "## 锚点目录\n\n| §B | x |\n[raw §A]\n\n---\n\n"
    "§A_CONTENT_START\n\npara A\n\n## §C Title\n\npara C"
)


class TestFourWayEquivalence:
    """四路等价锁:checks._extract_raw_anchors / runner._raw_anchor_set /
    distill._raw_anchor_positions / distill._declared_raw_anchors_with_pos
    对同一 raw 产出相同 canonical 集合(= raw_anchors.declared_anchor_set)。

    floor 消费 runner 传入的集,故 floor 同输入同集合由 transitivity 保证。
    """

    @pytest.mark.parametrize(
        "raw",
        [
            _FIXTURE_HEADING,
            _RAW_ARCHIVER,
            _MIXED,
            "",
            "no anchors at all",
        ],
        ids=["heading", "raw_archiver", "mixed", "empty", "no_anchors"],
    )
    def test_all_consumers_produce_same_canonical_set(self, raw):
        from src.akb2.quality.checks_structural import _extract_raw_anchors
        from src.akb2.quality.distill import (
            _declared_raw_anchors_with_pos,
            _raw_anchor_positions,
        )
        from src.akb2.quality.runner import _raw_anchor_set

        expected = declared_anchor_set(raw)
        assert set(_extract_raw_anchors(raw)) == expected
        assert _raw_anchor_set(raw) == expected
        assert {a for a, _ in _raw_anchor_positions(raw)} == expected
        assert {a for _, a in _declared_raw_anchors_with_pos(raw)} == expected
