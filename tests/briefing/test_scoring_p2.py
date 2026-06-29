"""P2-2 novelty 语义去重 + P2-3 engagement velocity。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from akb2.briefing import scoring


class FakeEmbed:
    def __init__(self, mapping):
        self.mapping = mapping

    def embed(self, texts):
        return [self.mapping.get(t, [0.0, 1.0]) for t in texts]


def _now_iso(hours_ago):
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()


def _t(text, hours_ago=1, **kw):
    t = {"text": text, "author": "@x", "created_at": _now_iso(hours_ago),
         "metrics": {"likes": 100}}
    t.update(kw)
    return t


# ---------------------------------------------------------------------------
# P2-2：novelty 语义去重（改写/转发的同义推文判重）
# ---------------------------------------------------------------------------

def test_novelty_semantic_dedup_near_duplicate():
    a = _t("OpenAI released GPT-5 with major reasoning gains today.")
    b = _t("GPT-5 is out from OpenAI, big jump in reasoning.")  # 同义改写
    m = {a["text"]: [1.0, 0.0, 0.0], b["text"]: [0.99, 0.01, 0.0]}  # cos≈1
    for an in scoring.TOPIC_ANCHORS:
        m[an] = [0.0, 0.0, 1.0]
    eng = scoring.ScoreEngine(embed_client=FakeEmbed(m))
    out = eng.score_batch([a, b])
    assert out[0]["dimensions"]["novelty"] == 1.0   # 首条
    assert out[1]["dimensions"]["novelty"] == 0.0   # 同义改写判重


def test_novelty_keeps_heterogeneous_same_topic():
    a = _t("GPT-5 reasoning benchmark results are impressive.")
    b = _t("New paper on diffusion models for protein folding.")  # 异质
    m = {a["text"]: [1.0, 0.0, 0.0], b["text"]: [0.0, 1.0, 0.0]}  # cos=0
    for an in scoring.TOPIC_ANCHORS:
        m[an] = [0.0, 0.0, 1.0]
    eng = scoring.ScoreEngine(embed_client=FakeEmbed(m))
    out = eng.score_batch([a, b])
    assert out[0]["dimensions"]["novelty"] == 1.0
    assert out[1]["dimensions"]["novelty"] == 1.0   # 不误判重


def test_novelty_falls_back_to_hash_without_embedding():
    a = _t("identical text")
    b = _t("identical text")
    eng = scoring.ScoreEngine()  # 无 embed → SHA1 精确去重
    out = eng.score_batch([a, b])
    assert out[0]["dimensions"]["novelty"] == 1.0
    assert out[1]["dimensions"]["novelty"] == 0.0


# ---------------------------------------------------------------------------
# P2-3：engagement velocity（高速新帖 > 同总量老爆款）
# ---------------------------------------------------------------------------

def test_velocity_new_beats_old_same_total():
    metrics = {"likes": 5000, "retweets": 1000, "replies": 500}
    new = _t("hot new post", hours_ago=1, metrics=metrics)
    old = _t("old viral post", hours_ago=120, metrics=metrics)
    assert scoring.engagement(new) > scoring.engagement(old)


def test_velocity_missing_created_at_no_crash():
    t = {"text": "no date", "metrics": {"likes": 1000}}  # 无 created_at
    val = scoring.engagement(t)  # 不应抛异常
    assert 0.0 <= val <= 1.0
