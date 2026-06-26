"""test_admission_novelty_wiring.py — step-1 Decision-2 洗稿生产接线。

覆盖 SC-9:
    - production_loads_wiki_refs: 生产 runner 准入段装载 vault wiki_references
    - rewrite_rejected_in_runner_path: 洗稿在生产 runner 路径被 novelty 拦（非仅 fixture 手传）
    - production_empty_index_holds_or_rejects: 生产 configured_but_empty/load_error →
      ledger 可见并 hold/reject，不静默 pass
    - empty_index_preserves_golden_with_explicit_allow_empty: golden 仅在显式 allow_empty
      （first_run_no_index）空索引下仍过（零回归）

loader 可 monkeypatch 注入；vault 结构用 tmp_path 构造，不读真 vault（hermetic）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.akb2.quality import runner


def _make_config(tmp_path: Path) -> runner.RunnerConfig:
    return runner.RunnerConfig(
        raw_dir=str(tmp_path / "raw"),
        ledger_path=str(tmp_path / "ledger.jsonl"),
        staging_dir=str(tmp_path / "staging"),
        vault_root=str(tmp_path / "vault"),
        failures_path=str(tmp_path / "failures.jsonl"),
        grader_result_dir=str(tmp_path / "grader"),
    )


def _build_vault(tmp_path: Path) -> Path:
    """构造受控 vault：wiki/concepts/*.md + wiki/sources/*.md（带 TLDR）。"""
    akb2_root = tmp_path / "vault_root"
    concepts = akb2_root / "wiki" / "concepts"
    sources = akb2_root / "wiki" / "sources"
    concepts.mkdir(parents=True)
    sources.mkdir(parents=True)
    (concepts / "loop-engineering.md").write_text(
        "# Loop Engineering\n\n## TLDR\nBounded convergent observable agent loops.\n",
        encoding="utf-8",
    )
    (concepts / "harness-engineering.md").write_text(
        "# Harness Engineering\n\n## TLDR\nAgent runtime and tool definitions.\n",
        encoding="utf-8",
    )
    (sources / "addy-osmani-loop-engineering.md").write_text(
        "# Addy Osmani\n\n## TLDR\nThe art of loop engineering for AI agents.\n",
        encoding="utf-8",
    )
    return akb2_root


# ---------------------------------------------------------------------------
# SC-9a: 生产装载 wiki_references
# ---------------------------------------------------------------------------


class TestProductionLoadsWikiRefs:
    def test_production_loads_wiki_refs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_load_vault_concept_index 从 vault 装载 wiki_references / known_concepts。"""
        akb2_root = _build_vault(tmp_path)
        staging_root = tmp_path / "staging_root"
        staging_root.mkdir()
        monkeypatch.setattr(runner, "get_akb2_root", lambda: akb2_root, raising=False)
        # loader 内部 late-import paths.get_akb2_root/get_staging_root → patch 源模块
        import src.akb2.loops.paths as paths_mod

        monkeypatch.setattr(paths_mod, "get_akb2_root", lambda: akb2_root)
        monkeypatch.setattr(paths_mod, "get_staging_root", lambda: staging_root)

        config = _make_config(tmp_path)
        index = runner._load_vault_concept_index(config, exclude_slug="some-other-source")

        assert index["status"] == runner.LANE_INDEX_STATUS_OK
        assert index["known_concepts"], "应装载 wiki/concepts slug"
        assert "loop-engineering" in index["known_concepts"]
        assert index["wiki_references"], "应装载 wiki 页 TLDR 摘要"
        assert any("agent loops" in r or "loop engineering" in r.lower() for r in index["wiki_references"])

    def test_loader_excludes_current_source_slug(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """loader 排除当前处理源自身 slug（防自比对误判洗稿）。"""
        akb2_root = _build_vault(tmp_path)
        staging_root = tmp_path / "staging_root"
        staging_root.mkdir()
        import src.akb2.loops.paths as paths_mod

        monkeypatch.setattr(paths_mod, "get_akb2_root", lambda: akb2_root)
        monkeypatch.setattr(paths_mod, "get_staging_root", lambda: staging_root)

        config = _make_config(tmp_path)
        index = runner._load_vault_concept_index(
            config, exclude_slug="addy-osmani-loop-engineering"
        )
        # 被排除的源不应出现在 source_paths
        assert all(
            "addy-osmani-loop-engineering" not in p for p in index["source_paths"]
        )


# ---------------------------------------------------------------------------
# SC-9b: 洗稿在 runner 路径被拦
# ---------------------------------------------------------------------------


class TestRewriteRejectedInRunnerPath:
    def test_rewrite_rejected_in_runner_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """洗稿在生产 runner 路径被 novelty 拦（loader 装载 wiki_references → 真 novelty fail）。"""
        config = _make_config(tmp_path)

        # loader 注入含 wiki_references 的 ok 索引（模拟既有知识库非空）
        monkeypatch.setattr(
            runner,
            "_load_vault_concept_index",
            lambda cfg, exclude_slug="": {
                "status": runner.LANE_INDEX_STATUS_OK,
                "wiki_references": ["Loop Engineering: bounded convergent agent loops"],
                "known_concepts": ["loop-engineering"],
                "pending_queue": [],
                "counts": {},
                "source_paths": [],
            },
        )
        monkeypatch.setattr(runner, "_existing_source_page_for_raw", lambda *a, **k: None)

        # mock route_llm：topic/how/lane pass，novelty（有 wiki_references 时触发）fail
        import src.akb2.quality.llm_router as router_mod
        from src.akb2.quality.llm_router import LLMResponse

        def _mock_route(model_family, messages, response_schema=None, **kwargs):
            sys_prompt = messages[0]["content"] if messages else ""
            if "新颖度" in sys_prompt or "增量信息" in sys_prompt:
                return LLMResponse(
                    content={"pass": False, "reason": "与既有 wiki 高度重叠，零增量"},
                    model_used="gemini-2.5-flash",
                )
            if "概念型" in sys_prompt or "操作型" in sys_prompt:
                return LLMResponse(
                    content={"content_lane": "procedural", "reason": "含命令"},
                    model_used="gemini-2.5-flash",
                )
            return LLMResponse(
                content={"pass": True, "reason": "ok"}, model_used="gemini-2.5-flash"
            )

        monkeypatch.setattr(router_mod, "route_llm", _mock_route)

        # 含命令的 raw（确定性 procedural；novelty 仍据 wiki_references 判洗稿）
        rewrite_raw = "# Rewrite\n\n1. Run npm test\n```bash\nmake deploy\n```\n"
        result = runner._run_pipeline_segments(
            "raw/rewrite--raw--2026-06-26.md", rewrite_raw, config
        )

        assert result["final_status"] == "rejected", "洗稿应被准入 novelty 拒收"
        assert "novelty" in (result["error"] or "")


# ---------------------------------------------------------------------------
# SC-9c: 生产空/错索引 hold/reject + ledger 可见
# ---------------------------------------------------------------------------


class TestProductionEmptyIndexHoldsOrRejects:
    @pytest.mark.parametrize(
        "bad_status",
        [runner.LANE_INDEX_STATUS_EMPTY, runner.LANE_INDEX_STATUS_LOAD_ERROR],
    )
    def test_production_empty_index_holds_or_rejects(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_status: str
    ) -> None:
        """生产 configured_but_empty/load_error → hold/reject，ledger 写 novelty_index_unavailable。"""
        config = _make_config(tmp_path)
        monkeypatch.setattr(
            runner,
            "_load_vault_concept_index",
            lambda cfg, exclude_slug="": {
                "status": bad_status,
                "wiki_references": [],
                "known_concepts": [],
                "pending_queue": [],
                "counts": {},
                "source_paths": [],
            },
        )
        monkeypatch.setattr(runner, "_existing_source_page_for_raw", lambda *a, **k: None)

        result = runner._run_pipeline_segments(
            "raw/x--raw--2026-06-26.md", "# X\n\nsome body\n", config
        )

        assert result["final_status"] == "rejected", "生产空/错索引不得静默放行"
        assert "novelty_index_unavailable" in (result["error"] or "")

        lines = [
            json.loads(line)
            for line in Path(config.ledger_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        abort_events = [
            e
            for e in lines
            if e["event"] == "abort"
            and e["details"].get("reason") == "novelty_index_unavailable"
        ]
        assert abort_events, "ledger 必须可见 novelty_index_unavailable"
        assert abort_events[0]["details"]["index_status"] == bad_status


# ---------------------------------------------------------------------------
# SC-9d: golden 在显式 allow_empty（first_run）下空索引零回归
# ---------------------------------------------------------------------------


class TestEmptyIndexPreservesGoldenWithExplicitAllowEmpty:
    def test_empty_index_preserves_golden_with_explicit_allow_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """first_run_no_index（显式 allow_empty）下空索引仍允许准入通过（golden 零回归）。"""
        config = _make_config(tmp_path)
        monkeypatch.setattr(
            runner,
            "_load_vault_concept_index",
            lambda cfg, exclude_slug="": {
                "status": runner.LANE_INDEX_STATUS_FIRST_RUN,
                "wiki_references": [],
                "known_concepts": [],
                "pending_queue": [],
                "counts": {},
                "source_paths": [],
            },
        )
        monkeypatch.setattr(runner, "_existing_source_page_for_raw", lambda *a, **k: None)

        import src.akb2.quality.llm_router as router_mod
        from src.akb2.quality.llm_router import LLMResponse

        def _mock_route(model_family, messages, response_schema=None, **kwargs):
            sys_prompt = messages[0]["content"] if messages else ""
            if "概念型" in sys_prompt or "操作型" in sys_prompt:
                return LLMResponse(
                    content={"content_lane": "procedural", "reason": "含命令"},
                    model_used="gemini-2.5-flash",
                )
            return LLMResponse(
                content={"pass": True, "reason": "ok"}, model_used="gemini-2.5-flash"
            )

        monkeypatch.setattr(router_mod, "route_llm", _mock_route)
        # 下游 distill 停链（聚焦验证 admission 在空索引 first_run 下未被拒）
        monkeypatch.setattr(
            runner,
            "_run_distill_segment",
            lambda *a, **k: (False, "extract_failed: stop", "stop", None, {}),
        )

        golden_raw = "# Golden\n\n1. Run npm test\n```bash\nmake deploy\n```\n"
        result = runner._run_pipeline_segments(
            "raw/golden--raw--2026-06-26.md", golden_raw, config
        )

        # 准入未因空索引被拒（停链点应在 distill 而非 admission novelty_index_unavailable）
        assert "novelty_index_unavailable" not in (result["error"] or "")
        lines = [
            json.loads(line)
            for line in Path(config.ledger_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        admission_pass = [
            e for e in lines if e["event"] == "admission" and e["details"].get("passed")
        ]
        assert admission_pass, "first_run 空索引下 golden 应通过准入"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
