"""test_f39_throughput.py — F39 入库效率两刀(质量线零动)。

F38 域内批次 25 篇漏斗尸检(2026-07-11):
  刀1 taxonomy 扩锚——6/25 死于 topic_relevance,死者全是 LLM 架构/可解释性/
      RAG 深文(Raschka/EleutherAI/dbreunig)。扩收录学科范围(用户授权),
      机制/证据/新颖度判定标准零动;与 concept-index-injection.md 两端同步。
  刀2 基础设施性 abort 自动重审——GPT-5.4 裁判宕机/deepseek 超时类死亡是
      "未被任何质量判定否决的白丢"(批次 2+ 篇)。有界(每篇一生一次,具名
      预算),重审重跑全部闸门;内容性死因(不收敛/地板/组件不足)不触发。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.akb2.quality import runner
from src.akb2.quality.admission import _DEFAULT_DOMAIN_TAXONOMY
from src.akb2.quality.runner import (
    AUTO_INFRA_REQUEUE_MAX,
    _is_infra_error,
    _is_processed,
    _maybe_auto_infra_requeue,
)

# ---------------------------------------------------------------------------
# 刀1: taxonomy 扩锚
# ---------------------------------------------------------------------------


class TestTaxonomyExpansion:
    def test_llm_mechanism_anchors_present(self) -> None:
        for anchor in ("LLM Architecture & Training",
                       "LLM Inference & Interpretability",
                       "RAG & Retrieval Engineering"):
            assert anchor in _DEFAULT_DOMAIN_TAXONOMY

    def test_original_anchors_retained(self) -> None:
        """扩锚不缩圈:原有全部锚点保留(收录面只增不减)。"""
        for anchor in ("AI Agent", "Loop Engineering", "Context Engineering",
                       "Harness Engineering", "Eval Engineering", "MCP",
                       "UI/UX Design", "Game Design"):
            assert anchor in _DEFAULT_DOMAIN_TAXONOMY

    def test_injection_doc_in_sync(self) -> None:
        """两端同步(注入文档明文要求)。"""
        doc = (Path(__file__).resolve().parents[2]
               / "skillpack" / "quality" / "concept-index-injection.md"
               ).read_text(encoding="utf-8")
        for anchor in ("LLM Architecture & Training",
                       "LLM Inference & Interpretability",
                       "RAG & Retrieval Engineering"):
            assert anchor in doc


# ---------------------------------------------------------------------------
# 刀2: 基础设施性 abort 自动重审
# ---------------------------------------------------------------------------


class TestInfraErrorClassifier:
    def test_infra_deaths_classified(self) -> None:
        """真机死因原文全部命中(sshh-fc392/R3/R7 实录)。"""
        for err in (
            "GPT-5.4 裁判不可用，Gemini fallback 只记录诊断，不能作为通过条件",
            "评分器双不可用，稿件挂起",
            "抽取调用失败（3/3）: LLMTimeoutError",
            "llm_failed: LLMTimeoutError",
        ):
            assert _is_infra_error(err), err

    def test_content_deaths_not_classified(self) -> None:
        """内容性死因零触发(质量判定的否决不重掷)。"""
        for err in (
            "修复不收敛: 5 项缺陷残留",
            "完整性地板未过（不可补偿）: concept F4：0 eligible 因果边",
            "concept deterministic fallback fail-closed: 1 组件 / 0 eligible 因果边",
            "准入未通过: topic_relevance",
            "compose_self_gate_failed: A2-frontmatter@frontmatter.related_concepts",
        ):
            assert not _is_infra_error(err), err


def _mk_config(tmp_path: Path):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("", encoding="utf-8")

    class _Cfg:
        ledger_path = str(ledger)
        # _log_event 仅用 ledger_path(append_event);其余字段不触达

    return _Cfg(), ledger


class TestAutoInfraRequeue:
    def test_budget_named(self) -> None:
        assert AUTO_INFRA_REQUEUE_MAX == 1  # 宪法 P1 具名预算

    def test_infra_abort_requeues_once(self, tmp_path: Path) -> None:
        cfg, ledger = _mk_config(tmp_path)
        ok = _maybe_auto_infra_requeue(cfg, "h1", "GPT-5.4 裁判不可用，...")
        assert ok is True
        lines = [json.loads(x) for x in ledger.read_text().splitlines()]
        assert lines[-1]["event"] == "requeue"
        assert lines[-1]["details"]["reason"].startswith("auto_infra_retry(F39)")

    def test_second_infra_abort_not_requeued(self, tmp_path: Path) -> None:
        """预算一生一次:故障窗口内不无限循环。"""
        cfg, ledger = _mk_config(tmp_path)
        assert _maybe_auto_infra_requeue(cfg, "h1", "评分器双不可用") is True
        assert _maybe_auto_infra_requeue(cfg, "h1", "评分器双不可用") is False
        lines = [json.loads(x) for x in ledger.read_text().splitlines()]
        assert sum(1 for e in lines if e["event"] == "requeue") == 1

    def test_content_abort_not_requeued(self, tmp_path: Path) -> None:
        cfg, ledger = _mk_config(tmp_path)
        assert _maybe_auto_infra_requeue(cfg, "h1", "修复不收敛: 5 项缺陷残留") is False
        assert ledger.read_text() == ""

    def test_requeue_reopens_article_f26_semantics(self, tmp_path: Path) -> None:
        """闭环:abort 终态 + auto requeue → _is_processed=False(重新可处理)。"""
        cfg, ledger = _mk_config(tmp_path)
        with open(ledger, "a", encoding="utf-8") as f:
            f.write(json.dumps({"loop_id": "loop1", "node": "abort",
                                "event": "abort", "content_hash": "h1",
                                "ts": "2026-07-11T00:00:00Z", "details": {}}) + "\n")
        assert _is_processed("h1", str(ledger)) is True
        _maybe_auto_infra_requeue(cfg, "h1", "抽取调用失败（3/3）: LLMTimeoutError")
        assert _is_processed("h1", str(ledger)) is False

    def test_hooks_wired_at_abort_sites(self) -> None:
        """源码断言:distill 与 repair/grade 两个 abort 落账点都挂了钩(防回退)。"""
        import inspect

        src = inspect.getsource(runner)
        assert src.count("_maybe_auto_infra_requeue(config, content_hash,") >= 2
