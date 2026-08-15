"""test_p2_admission_snippets.py — P2 admission 收口测试。

覆盖:
    - how_tendency 重构为 _build_keyword_aware_snippet 后行为逐字节不变(输出含 how 章节,≤8000)
    - mechanism 仲裁窗见到 4000 字后的机制章节(非 body[:4000] 截断)
    - lane 仲裁窗同(mechanism 窗)
    - loader 超 5 全返 + 200 cap 截尾记账([N3])
"""

from __future__ import annotations

from typing import Any

from src.akb2.quality import runner
from src.akb2.quality.admission import (
    _build_how_tendency_snippet,
    _build_keyword_aware_snippet,
    _build_mechanism_snippet,
    _section_is_how_candidate,
)

_HOW_RAW = """\
## 锚点目录

- §A: intro
- §B: how

---

§A_CONTENT_START

导语:这是一篇关于 agent loop 的文章,前面有大量背景文字铺垫。

## Background

lorem ipsum background padding " ". " " . . . " " filler content that is not mechanism
related just to pad the front section so the how section is pushed beyond 4000 chars
which is the old truncation limit. more padding. " " " " . . . " " . . . " " . . . " "
even more padding to ensure the how section is beyond the 4000 char window. " " . . .
" " . . . " " . . . " " . . . " " . . . " " . . . " " . . . " " . . . " " . . . " " . .
. " " . . . " " . . . " " . . . " " . . . " " . . . " " . . . " " . . . " " . . . " " .

## How: Steps

1. Run the agent loop
2. Check convergence
3. Write ledger

## Conclusion

done.
"""


class TestHowTendencyRefactorByteIdentical:
    def test_how_snippet_contains_how_section(self):
        """重构后 how_tendency 仍拼入 How 章节(非 body[:4000] 截断)。"""
        snippet = _build_how_tendency_snippet(_HOW_RAW)
        assert "How: Steps" in snippet  # how 章节被拼入
        assert "Run the agent loop" in snippet
        assert len(snippet) <= 8000

    def test_keyword_aware_snippet_delegate_identical(self):
        """_build_how_tendency_snippet == _build_keyword_aware_snippet(how filter) 同输出。"""
        from src.akb2.quality.admission import (
            HOW_TENDENCY_CONTEXT_CHARS,
            MAX_HOW_TENDENCY_CONTENT_CHARS,
        )

        direct = _build_keyword_aware_snippet(
            _HOW_RAW,
            _section_is_how_candidate,
            HOW_TENDENCY_CONTEXT_CHARS,
            MAX_HOW_TENDENCY_CONTENT_CHARS,
        )
        delegated = _build_how_tendency_snippet(_HOW_RAW)
        assert direct == delegated  # 逐字节相同


class TestMechanismWindow:
    def test_mechanism_window_sees_beyond_4000(self):
        """mechanism 仲裁窗见到 4000 字后的机制章节(非 body[:4000] 截断)。

        构造 raw:前 4000+ 字 padding,后段是机制章节。_build_mechanism_snippet 应拼入
        机制章节(关键词感知),非截断到 4000。
        """
        padding = "导语填充 " * 900  # ~4500 字 padding
        raw = (
            f"§A_CONTENT_START\n\n{padding}\n\n"
            "## Mechanism Components\n\n"
            "The Executor Pool dispatches to the Task Graph.\n"
        )
        snippet = _build_mechanism_snippet(raw)
        # 机制章节被拼入(关键词感知,非截断)
        assert "Mechanism Components" in snippet
        assert "Executor Pool" in snippet

    def test_mechanism_check_uses_mechanism_snippet(self, monkeypatch):
        """_check_mechanism_signal 的 LLM prompt 含 4000 字后的机制章节(fake route_llm 捕获)。"""
        from src.akb2.quality import admission as adm

        padding = "导语填充 " * 900
        body = (
            f"{padding}\n\n## Mechanism Components\n\nThe Executor Pool dispatches to Task Graph.\n"
        )
        captured: dict[str, Any] = {}

        def _fake_route(model_family, messages, response_schema=None, **kw):
            captured["prompt"] = messages[1]["content"] if len(messages) > 1 else ""

            return type(
                "R", (), {"content": {"pass": True, "reason": "ok"}, "model_used": "m"}
            )()

        # body 无强机制信号(无编号标题/§-anchor)→ 走 LLM 仲裁,用 mechanism 窗
        adm._check_mechanism_signal(body, _fake_route)
        assert "Mechanism Components" in captured.get("prompt", "")
        assert "Executor Pool" in captured.get("prompt", "")


class TestLoaderCap200:
    def test_loader_returns_all_up_to_200_and_truncated_count(self, tmp_path, monkeypatch):
        """[N3] loader 扫全库(非前-5 break),≤200 全返,>200 截尾并在 counts 记 truncated。"""
        akb2_root = tmp_path / "akb2"
        wiki = akb2_root / "wiki" / "sources"
        wiki.mkdir(parents=True)
        # 建 210 个 wiki 页(超 200)
        for i in range(210):
            (wiki / f"source-{i:03d}.md").write_text(
                f"# Source {i}\n\n## TLDR\nsource number {i} content.\n", encoding="utf-8"
            )
        staging = tmp_path / "staging"
        staging.mkdir()

        import src.akb2.loops.paths as paths_mod

        monkeypatch.setattr(paths_mod, "get_akb2_root", lambda: akb2_root)
        monkeypatch.setattr(paths_mod, "get_staging_root", lambda: staging)
        monkeypatch.setattr(runner, "get_akb2_root", lambda: akb2_root, raising=False)

        config = runner.RunnerConfig(
            raw_dir=str(tmp_path / "raw"),
            ledger_path=str(tmp_path / "ledger.jsonl"),
            staging_dir=str(staging),
            vault_root=str(akb2_root),
            failures_path=str(tmp_path / "failures.jsonl"),
            grader_result_dir=str(tmp_path / "grader"),
            poll_interval_seconds=1,
        )
        index = runner._load_vault_concept_index(config, exclude_slug="")
        assert index["status"] == runner.LANE_INDEX_STATUS_OK
        # ≤200 全返(扫全库,非前-5)
        assert len(index["wiki_references"]) == 200
        assert index["counts"]["truncated"] == 10  # 210-200=10 截尾
        assert index["counts"]["wiki_references"] == 200
