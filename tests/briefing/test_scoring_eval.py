"""P1-7 打分排序 eval harness 测试：指标函数正确性 + 语义版对比（mock 嵌入，确定性）。

run.py 在 evals/briefing-scoring/ 下（非 src），用 importlib 按路径加载。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from akb2.briefing import scoring

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUN_PY = _REPO_ROOT / "evals" / "briefing-scoring" / "run.py"
_LABELED = _REPO_ROOT / "evals" / "briefing-scoring" / "fixtures" / "labeled.json"


def _load_run():
    spec = importlib.util.spec_from_file_location("scoring_eval_run", _RUN_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


run = _load_run()


# ---------------------------------------------------------------------------
# 指标函数（纯函数，确定性）
# ---------------------------------------------------------------------------

def _t(label, score, author="@a", text="x"):
    return {"label": label, "score": score, "author": author, "text": text,
            "dimensions": {"topic_relevance": 0.0}}


def test_precision_at_k_perfect():
    ranked = [_t(1, 0.9), _t(1, 0.8), _t(0, 0.1)]
    assert run.precision_at_k(ranked, 2) == 1.0


def test_precision_at_k_mixed():
    ranked = [_t(1, 0.9), _t(0, 0.8), _t(1, 0.7), _t(0, 0.1)]
    assert run.precision_at_k(ranked, 2) == 0.5


def test_ndcg_perfect_ranking_is_one():
    ranked = [_t(1, 0.9), _t(1, 0.8), _t(0, 0.2), _t(0, 0.1)]
    assert run.ndcg_at_k(ranked, 4) == pytest.approx(1.0)


def test_ndcg_worst_ranking_below_perfect():
    good = [_t(1, 0.9), _t(1, 0.8), _t(0, 0.2), _t(0, 0.1)]
    bad = [_t(0, 0.9), _t(0, 0.8), _t(1, 0.2), _t(1, 0.1)]
    assert run.ndcg_at_k(bad, 4) < run.ndcg_at_k(good, 4)


def test_ndcg_all_negative_is_zero():
    ranked = [_t(0, 0.9), _t(0, 0.1)]
    assert run.ndcg_at_k(ranked, 2) == 0.0


def test_victim_rescue_counts_only_victims_in_topk():
    vk = {("@cn", "中文AI")}
    ranked = [_t(1, 0.9, "@cn", "中文AI"), _t(1, 0.8, "@en", "AI model"), _t(0, 0.1)]
    assert run.victim_rescue_at_k(ranked, vk, 1) == 1
    assert run.victim_rescue_at_k(ranked, vk, 3) == 1  # 只有 1 个受害者


# ---------------------------------------------------------------------------
# 标注集与受害者识别（绑真实 fixture）
# ---------------------------------------------------------------------------

def test_load_labeled_fixture():
    tweets = run.load_labeled(_LABELED)
    assert len(tweets) == 45
    assert sum(1 for t in tweets if t["label"] == 1) == 30


def test_keyword_victims_are_chinese_or_no_buzzword_ai():
    tweets = run.load_labeled(_LABELED)
    victims = run.keyword_victim_ids(tweets)
    # 标注集设计：≥10 个关键词受害者（中文 AI + 无 buzzword AI）
    assert len(victims) >= 10
    # 每个受害者都是 label=1 且关键词 relevance=0
    for author, text in victims:
        t = next(x for x in tweets if x["author"] == author and x["text"] == text)
        assert t["label"] == 1
        assert scoring._topic_relevance_keywords(t) == 0.0


# ---------------------------------------------------------------------------
# 语义版对比（mock 嵌入：AI→锚点向量，离题→正交向量）
# ---------------------------------------------------------------------------

class _FakeEmbed:
    """label=1 的文本 → [1,0]（与锚点 cos=1）；label=0 → [0,1]（cos=0）。"""

    def __init__(self, tweets):
        self._by_text = {t["text"]: ([1.0, 0.0] if t["label"] == 1 else [0.0, 1.0]) for t in tweets}

    def embed(self, texts):
        out = []
        for t in texts:
            if t in self._by_text:
                out.append(self._by_text[t])
            else:
                out.append([1.0, 0.0])  # 锚点 → [1,0]
        return out


def test_semantic_beats_keyword_on_fixture():
    tweets = run.load_labeled(_LABELED)
    k = sum(1 for t in tweets if t["label"] == 1)
    fake_engine = scoring.ScoreEngine(embed_client=_FakeEmbed(tweets))
    metrics = run.evaluate(tweets, k, semantic=True, semantic_engine=fake_engine)

    kw, sem = metrics["keyword"], metrics["semantic"]
    # 语义版救回全部关键词受害者，关键词版救不回
    assert sem["victim_rescue_at_k"] > kw["victim_rescue_at_k"]
    # 语义版 nDCG / precision 均 ≥ 关键词版 → 验收通过
    assert sem["ndcg_at_k"] >= kw["ndcg_at_k"]
    assert sem["precision_at_k"] >= kw["precision_at_k"]
    assert run.acceptance(metrics) is True


def test_semantic_silent_fallback_raises():
    """嵌入静默回退（受害者 topic_relevance 全 0）必须报错，不产假对比。"""
    tweets = run.load_labeled(_LABELED)
    k = 30

    class _FallbackEngine:
        # 模拟 score_batch 静默回退关键词版：受害者 topic_relevance 全 0
        def score_batch(self, ts):
            return scoring.ScoreEngine().score_batch(ts)

    with pytest.raises(RuntimeError, match="静默回退"):
        run.evaluate(tweets, k, semantic=True, semantic_engine=_FallbackEngine())
