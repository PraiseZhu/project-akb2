"""test_scoring_degradation.py — 缺陷 #6 附带：嵌入网关失败时语义去重静默降级。

`score_batch` 的 except 把 embeddings 置 None 就继续（novelty 退化为 sha1 精确去重，
改写/转发的近重复漏检），既不打日志也不留标记 —— 违反宪法原则 4「失败可见」。
"""
from __future__ import annotations


def _tweet(text: str, tid: str = "1") -> dict:
    return {"id": tid, "author": "@sama", "text": text, "created_at": "2026-05-30T02:00:00Z"}


class _FailingEmbed:
    def embed(self, texts):
        raise RuntimeError("gateway 503")


class _StubEmbed:
    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


def test_embed_failure_is_visible_and_flagged(capsys):
    """嵌入失败 → stderr 有 WARNING，engine 暴露 embed_degraded 与 exact_sha1 判重模式。"""
    from akb2.briefing.scoring import ScoreEngine

    engine = ScoreEngine(embed_client=_FailingEmbed())
    out = engine.score_batch([_tweet("AI agents are here")])

    err = capsys.readouterr().err
    assert "WARNING" in err and "gateway 503" in err, "嵌入失败必须可见（含失败原因）"
    assert engine.embed_degraded is True, "降级必须留可检查标记"
    assert engine.novelty_mode == "exact_sha1", "语义不可用时判重模式应为 sha1 精确"
    assert len(out) == 1, "降级后仍应产出打分（不崩）"


def test_semantic_mode_reported_when_embeddings_work():
    """嵌入可用时 novelty_mode 报 semantic（标记须反映真实模式，不能恒定）。"""
    from akb2.briefing.scoring import ScoreEngine

    engine = ScoreEngine(embed_client=_StubEmbed())
    engine.score_batch([_tweet("AI agents are here")])
    assert engine.novelty_mode == "semantic"
    assert engine.embed_degraded is False


def test_run_scoring_reports_novelty_mode(capsys):
    """pipeline.run_scoring 必须把判重模式回传（供 frontmatter 落盘）并把降级说清。"""
    from akb2.briefing.pipeline import run_scoring

    scored = run_scoring({"tweets": [_tweet("AI agents are here")]})
    assert scored["novelty_mode"] == "exact_sha1"
    err = capsys.readouterr().err
    assert "novelty" in err.lower(), "关键词/精确去重降级必须在日志里说清"
