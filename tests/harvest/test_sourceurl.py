"""sc-asm-sourceurl 验收:source_url 常量策略 + 真实消费者行为断言。

策略 = ``cindy://session/<payload session_id>``,不做变换、不生成新 UUID。
唯一性职责在 sc-asm-schema(session_id 必为小写 canonical UUID4)。

① 基线清单锁定:现测读取点集合 == BASELINE_READERS(9 个,逐个标注性质);
② 逐消费者行为断言 (a)-(e) 全用真 uuid;
③ 预测红集:用固定常量的实现在 (b) 必红(所有 session 互相判重);
④ lint 对该字段零 finding(锁定当前无格式门控)。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
QUALITY = REPO_ROOT / "src" / "akb2" / "quality"

# 真实小写 canonical UUID4(session_id 合法形态;本 SC 用真值,不用 spy 自配)
UA = "11111111-2222-4333-8444-555555555555"
UB = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
URL_A = f"cindy://session/{UA}"
URL_B = f"cindy://session/{UB}"

UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def _raw_md(url: str, title: str = "T") -> str:
    return f"---\nsource_url: {url}\n---\n{title}\nbody\n"


# ---------------------------------------------------------------------------
# ① 基线清单锁定:现测读取点集合 == BASELINE_READERS(9 个)
# ---------------------------------------------------------------------------

# lead 实测 grep -rln source_url src/akb2/quality/ --include=*.py 得 9 个文件,
# 逐个标注读取性质;漏标文件下次改动时不会触发红灯。
BASELINE_READERS: dict[str, str] = {
    "quality/promote.py": "行为消费者:Gate-0 非空硬校验(promote.py:205-224)",
    "quality/runner.py": "行为消费者:去重定位(_existing_source_page_for_raw:405-431)",
    "quality/admission.py": "行为消费者:exact-novelty 匹配(_select_novelty_neighbors:699-724)",
    "quality/distill.py": "行为消费者:近邻过滤(_candidate_neighbor_paths:6226-6267)",
    "quality/synthesis_assembler.py": "行为消费者:读 raw frontmatter 取 url 进合成成员(_parse_member:61-101)",
    "quality/intake/raw_archiver.py": "写入侧:9 字段之一(_build_frontmatter)",
    "quality/calibration.py": "写入侧:构造 fixture(calibration.py:538)",
    "quality/checks_structural.py": "非消费者:仅提示文案字符串(checks_structural.py:1588)",
    "quality/intake/frontmatter_repair.py": "非消费者:仅断言修复产物自带该键,不读值做判定(frontmatter_repair.py:110)",
}


def _readers() -> dict[str, str]:
    """实测 source_url 读取点:grep 语义重现在测试内(production 代码,排除测试/脚本/视频)。"""
    found: dict[str, str] = {}
    rx = re.compile(r"source_url")
    for path in QUALITY.rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if path.name.startswith("test_") or "/evals/" in rel or "/scripts/" in rel:
            continue
        if rx.search(path.read_text(encoding="utf-8")):
            found[_rel_quality(path)] = ""
    return found


def _rel_quality(p: Path) -> str:
    """BASELINE_READERS 用 quality/... 相对 src/akb2/ 的形态,与 _readers 对齐。"""
    return p.relative_to(QUALITY.parent).as_posix()


class TestBaselineReaders:
    def test_baseline_readers_locked(self) -> None:
        """现测读取点集合 == BASELINE_READERS(新增消费者时此断言变红,提示重新定案)。"""
        assert set(_readers()) == set(BASELINE_READERS)
        # 逐个核对标注文件真实存在于该读取点集合
        for rel in BASELINE_READERS:
            assert (QUALITY.parent / rel).exists(), f"{rel} 不在 quality 下"

    def test_baseline_reader_annotations_cover_all_nine(self) -> None:
        """清单必须含全部 9 个并逐个标注性质(漏标文件下次改动时不触发红灯)。"""
        assert len(BASELINE_READERS) == 9
        for rel, note in BASELINE_READERS.items():
            assert note.strip(), f"{rel} 缺标注"
            assert any(k in note for k in (
                "行为消费者", "写入侧", "非消费者")), f"{rel} 标注性质不明: {note}"


# ---------------------------------------------------------------------------
# ② 逐消费者 cindy:// 行为断言(本 SC 的实质,全用真 uuid)
# ---------------------------------------------------------------------------


class TestConsumerBehavior:
    def test_a_promote_gate0_nonempty(self) -> None:
        """(a) promote._check_source_provenance 对 cindy://session/<uuid> 返回 True。"""
        from akb2.quality.promote import _check_source_provenance

        with pytest.MonkeyPatch.context() as mp:
            tmp = mp.setitem  # noqa: F841 (保持 import 形态直观)
            # 真文件路径:Gate-0 读候选页 frontmatter
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                cand = Path(td) / "cand.md"
                cand.write_text(_raw_md(URL_A), encoding="utf-8")
                ok, msg = _check_source_provenance(cand)
                assert ok is True
                assert "source_url" in msg

    def test_b_admission_exact_novelty_identity(self) -> None:
        """(b) admission exact-novelty:同 uuid 命中 duplicate_same_claim、异 uuid 不命中。"""
        from akb2.quality.admission import _select_novelty_neighbors

        raw_md = _raw_md(URL_A)
        refs_same = [{"slug": "", "source_url": URL_A, "title": "T", "tldr": "x"}]
        refs_diff = [{"slug": "", "source_url": URL_B, "title": "T", "tldr": "x"}]
        sel_same, verdict_same = _select_novelty_neighbors(raw_md, refs_same, k=3)
        _sel_diff, verdict_diff = _select_novelty_neighbors(raw_md, refs_diff, k=3)
        assert verdict_same == "duplicate_same_claim"
        assert all(r.get("_match_type") == "duplicate_same_claim" for r in sel_same)
        assert verdict_diff == "no_relevant_baseline"

    def test_c_distill_neighbor_skip_same_url(self) -> None:
        """(c) distill 近邻过滤:同 source_url 的既有 wiki 页被跳过、不同则进入候选。"""
        from akb2.quality.distill import _candidate_neighbor_paths

        with pytest.MonkeyPatch.context() as _:
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                td = Path(td)
                vault = td / "vault"
                (vault / "wiki" / "sources").mkdir(parents=True)
                (vault / "wiki" / "sources" / "same.md").write_text(
                    _raw_md(URL_A), encoding="utf-8")
                # diff 页:异 url,但 raw 文件名与页名共享 token → 应进入候选
                # (近邻 token 来自 raw_slug + raw_url + claims)
                (vault / "wiki" / "sources" / "shared-topic.md").write_text(
                    _raw_md(URL_B, title="shared-topic"), encoding="utf-8")
                raw = td / "raw-shared-topic.md"
                raw.write_text(_raw_md(URL_A), encoding="utf-8")
                neigh = _candidate_neighbor_paths(vault, raw, claims=[], limit=5)
                # 返回值为相对 vault_root 的 posix 路径(带 wiki/ 前缀)
                assert "wiki/sources/same.md" not in neigh  # 同 url 被 continue 跳过
                assert "wiki/sources/shared-topic.md" in neigh

    def test_d_runner_dedup_locate(self) -> None:
        """(d) runner 去重:按 source_url 能定位既有 source 页,不同 uuid 定位不到。"""
        from akb2.quality.runner import RunnerConfig, _existing_source_page_for_raw

        with pytest.MonkeyPatch.context() as _:
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                td = Path(td)
                vault = td / "vault"
                source_root = vault / "wiki" / "sources"
                source_root.mkdir(parents=True)
                (source_root / "existing.md").write_text(_raw_md(URL_A), encoding="utf-8")
                cfg = RunnerConfig(
                    raw_dir=str(td / "raw"), ledger_path=str(td / "ledger.jsonl"),
                    staging_dir=str(td / "stage"), vault_root=str(vault),
                    failures_path=str(td / "failures"),
                    grader_result_dir=str(td / "grader"))
                raw_a = td / "raw-a.md"
                raw_a.write_text(_raw_md(URL_A), encoding="utf-8")
                found = _existing_source_page_for_raw(str(raw_a), cfg)
                assert found is not None
                assert found.name == "existing.md"
                raw_b = td / "raw-b.md"
                raw_b.write_text(_raw_md(URL_B), encoding="utf-8")
                assert _existing_source_page_for_raw(str(raw_b), cfg) is None

    def test_e_synthesis_member_url_passthrough(self) -> None:
        """(e) synthesis 成员组装:raw 的 url 字段与传入值逐字相等且非空。"""
        from akb2.quality.synthesis_assembler import _parse_member

        with pytest.MonkeyPatch.context() as _:
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                raw = Path(td) / "raw.md"
                raw.write_text(_raw_md(URL_A), encoding="utf-8")
                member = _parse_member(raw)
                assert member.get("url") == URL_A
                assert member.get("url")


# ---------------------------------------------------------------------------
# ③ 预测红集:固定常量(非 per-session uuid)实现在 (b) 必红
# ---------------------------------------------------------------------------


class TestPredictedRed:
    def test_constant_url_would_merge_all_sessions(self) -> None:
        """用固定常量的实现:所有 session 的产出被 admission 判为同一来源。"""
        from akb2.quality.admission import _select_novelty_neighbors

        # 若 assembler 用常量,则不同 session 的 raw 也会带同一 source_url,
        # (b) 中「异 uuid 不命中」必然红 —— 本测试固化该红集的前提。
        CONSTANT_URL = "cindy://session/00000000-0000-4000-8000-000000000000"
        raw_md = _raw_md(CONSTANT_URL)
        refs_diff = [{"slug": "", "source_url": CONSTANT_URL,
                      "title": "T", "tldr": "x"}]
        sel, verdict = _select_novelty_neighbors(raw_md, refs_diff, k=3)
        assert verdict == "duplicate_same_claim"
        assert all(r.get("_match_type") == "duplicate_same_claim" for r in sel)


# ---------------------------------------------------------------------------
# ④ lint 对该字段零 finding(锁定当前无格式门控)
# ---------------------------------------------------------------------------


class TestLintNoGate:
    def test_lint_no_finding_for_source_url(self) -> None:
        """lint 对 source_url 字段不产生任何 finding(无格式门控)。"""
        import akb2.lint.akb2_lint as lint_mod
        assert hasattr(lint_mod, "LINT_RULES") or hasattr(lint_mod, "RULES")
        rules = getattr(lint_mod, "LINT_RULES", None) or getattr(lint_mod, "RULES", [])
        names = [getattr(r, "name", str(r)) if not isinstance(r, str) else r
                 for r in rules]
        assert not any("source_url" in n.lower() or "url" in n.lower() for n in names)


# ---------------------------------------------------------------------------
# assembler 侧:source_url 策略 + 无 uuid 生成(调用真实 assemble 需要部署根,
# 此处只做纯策略断言:从源码读常量策略,且禁止 uuid 生成调用)
# ---------------------------------------------------------------------------


class TestAssemblerStrategy:
    def test_assembler_has_no_uuid_generation(self) -> None:
        """assembler 内不得出现任何 uuid 生成调用(预测红集:出现即判红)。"""
        src = (REPO_ROOT / "src" / "akb2" / "harvest" / "assembler.py").read_text(
            encoding="utf-8")
        assert "uuid4" not in src
        assert "uuid.uuid" not in src
        assert "random" not in src

    def test_assembler_source_url_strategy_present(self) -> None:
        """assembler 里存在 cindy://session/ + session_id 的策略字样(不做变换)。"""
        src = (REPO_ROOT / "src" / "akb2" / "harvest" / "assembler.py").read_text(
            encoding="utf-8")
        assert "cindy://session/" in src
        assert "session_id" in src
