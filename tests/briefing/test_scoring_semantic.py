"""P1-3/4/5 scoring 语义内核测试：批量预嵌入 / 语义 topic_relevance / 失败降级 / 权重重平衡。"""
from __future__ import annotations

from akb2.briefing import scoring

_NOW = "2026-05-31T12:00:00+00:00"


class FakeEmbed:
    """可控向量的假嵌入客户端：text -> vec。记录调用以验证批量化。"""

    def __init__(self, mapping, *, fail=False):
        self.mapping = mapping
        self.calls = []
        self.fail = fail

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("embed gateway down")
        return [self.mapping.get(t, [0.0, 1.0]) for t in texts]


def _anchor_map():
    # 所有锚点 → [1,0]，使「相关」文本（也映射到 [1,0]）cos=1，离题（[0,1]）cos=0
    return {a: [1.0, 0.0] for a in scoring.TOPIC_ANCHORS}


def _tweet(text, **kw):
    t = {"text": text, "author": "@nobody", "created_at": _NOW, "metrics": {"likes": 10}}
    t.update(kw)
    return t


# ---------------------------------------------------------------------------
# P1-5：权重重平衡
# ---------------------------------------------------------------------------

def test_weights_sum_to_one_and_authority_downweighted():
    assert abs(sum(scoring.DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9
    assert scoring.DEFAULT_WEIGHTS["author_authority"] <= 0.10
    # 语义内核应是主信号
    assert scoring.DEFAULT_WEIGHTS["topic_relevance"] >= 0.30


# ---------------------------------------------------------------------------
# P1-4：语义 topic_relevance（捞回关键词漏掉的中文/无 buzzword AI）
# ---------------------------------------------------------------------------

def test_semantic_relevance_beats_keyword_on_chinese_ai():
    relevant = _tweet("国产大模型推理能力提升，中文理解超过上一代")  # 关键词命中=0
    offtopic = _tweet("homemade pizza recipe with fresh basil")
    m = _anchor_map()
    m[relevant["text"]] = [1.0, 0.0]   # cos=1 → 语义高分
    m[offtopic["text"]] = [0.0, 1.0]   # cos=0 → 语义低分
    eng = scoring.ScoreEngine(embed_client=FakeEmbed(m))
    out = eng.score_batch([relevant, offtopic])

    # 语义版：中文 AI > 离题
    assert out[0]["dimensions"]["topic_relevance"] >= 0.9
    assert out[1]["dimensions"]["topic_relevance"] <= 0.1
    # 对照：关键词版把中文 AI 判为 0（这正是窄门问题）
    assert scoring.topic_relevance(relevant) == 0.0


def test_semantic_relevance_normalization_clamps():
    # cos 低于 LOW → 0；高于 HIGH → 1
    t_low = _tweet("totally unrelated content about gardening")
    t_high = _tweet("AI large language model agent reasoning")
    m = _anchor_map()
    m[t_low["text"]] = [0.0, 1.0]
    m[t_high["text"]] = [1.0, 0.0]
    eng = scoring.ScoreEngine(embed_client=FakeEmbed(m))
    out = eng.score_batch([t_low, t_high])
    assert out[0]["dimensions"]["topic_relevance"] == 0.0
    assert out[1]["dimensions"]["topic_relevance"] == 1.0


# ---------------------------------------------------------------------------
# P1-3：批量预嵌入（不逐条联网）
# ---------------------------------------------------------------------------

def test_score_batch_embeds_once_not_per_tweet():
    tweets = [_tweet(f"AI model number {i}") for i in range(3)]
    m = _anchor_map()
    for t in tweets:
        m[t["text"]] = [1.0, 0.0]
    fe = FakeEmbed(m)
    eng = scoring.ScoreEngine(embed_client=fe)
    eng.score_batch(tweets)
    # 锚点一次 + 推文一次 = 2 次调用（不是每条一次）
    assert len(fe.calls) == 2
    assert len(fe.calls[1]) == 3  # 3 条推文一个批次


# ---------------------------------------------------------------------------
# P1-4 / SC-6：嵌入失败回退关键词版，pipeline 不崩
# ---------------------------------------------------------------------------

def test_score_batch_falls_back_to_keyword_on_embed_failure():
    t = _tweet("GPT-5 model agent reasoning")  # 关键词命中 ≥3
    eng = scoring.ScoreEngine(embed_client=FakeEmbed({}, fail=True))
    out = eng.score_batch([t])  # 不应抛异常
    assert "score" in out[0]
    # 回退到关键词版的 relevance
    assert out[0]["dimensions"]["topic_relevance"] == scoring.topic_relevance(t)


def test_score_batch_without_client_uses_keyword():
    t = _tweet("GPT-5 model agent")
    eng = scoring.ScoreEngine()  # 无 embed_client
    out = eng.score_batch([t])
    assert out[0]["dimensions"]["topic_relevance"] == scoring.topic_relevance(t)


# ---------------------------------------------------------------------------
# 向后兼容：旧 score_tweet 仍是关键词路径
# ---------------------------------------------------------------------------

def test_score_tweet_backward_compatible_keyword():
    t = _tweet("AI llm agent")
    eng = scoring.ScoreEngine()
    out = eng.score_tweet(t)
    assert out["dimensions"]["topic_relevance"] == scoring.topic_relevance(t)
