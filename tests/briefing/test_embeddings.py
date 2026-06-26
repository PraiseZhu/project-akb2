"""P1-1 embeddings.py 测试：批量 / 缓存 / 失败降级。

约定：patch 模块级 `_embed_api`（真实 httpx 调用入口），不联网。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from akb2.briefing import embeddings as emb

# ---------------------------------------------------------------------------
# batch：一次调用传数组返回对应向量（顺序一致）
# ---------------------------------------------------------------------------

def test_embed_batch_returns_vectors_in_order(tmp_path):
    texts = ["alpha", "beta", "gamma"]
    fake = {
        "alpha": [0.1, 0.2],
        "beta": [0.3, 0.4],
        "gamma": [0.5, 0.6],
    }

    def _fake_api(batch, **kwargs):
        return [fake[t] for t in batch]

    with patch.object(emb, "_embed_api", side_effect=_fake_api) as m:
        client = emb.EmbeddingClient(cache_dir=tmp_path)
        vecs = client.embed(texts)

    assert vecs == [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]
    # 一次批量调用（不是逐条）
    assert m.call_count == 1
    # 该次调用拿到的是全部 miss 文本
    assert list(m.call_args.args[0]) == texts


def test_embed_batch_only_fetches_cache_misses(tmp_path):
    """部分命中缓存时，只对 miss 文本发起 API。"""
    fake = {"a": [1.0], "b": [2.0], "c": [3.0]}

    def _fake_api(batch, **kwargs):
        return [fake[t] for t in batch]

    with patch.object(emb, "_embed_api", side_effect=_fake_api) as m:
        client = emb.EmbeddingClient(cache_dir=tmp_path)
        client.embed(["a", "b"])           # 预热 a,b
        first_calls = m.call_count
        out = client.embed(["a", "b", "c"])  # 只 c 是 miss

    assert out == [[1.0], [2.0], [3.0]]
    assert m.call_count == first_calls + 1
    # 第二次只发 c
    assert list(m.call_args.args[0]) == ["c"]


# ---------------------------------------------------------------------------
# cache：文本 hash 缓存命中不重复调网关（跨 client 实例，磁盘持久）
# ---------------------------------------------------------------------------

def test_embed_cache_hit_skips_api(tmp_path):
    calls = {"n": 0}

    def _fake_api(batch, **kwargs):
        calls["n"] += 1
        return [[0.9, 0.9] for _ in batch]

    with patch.object(emb, "_embed_api", side_effect=_fake_api):
        c1 = emb.EmbeddingClient(cache_dir=tmp_path)
        c1.embed(["same-text"])
        assert calls["n"] == 1
        # 新实例、同 cache_dir → 磁盘缓存命中，不再调 API
        c2 = emb.EmbeddingClient(cache_dir=tmp_path)
        c2.embed(["same-text"])
        assert calls["n"] == 1


def test_embed_cache_keyed_by_model(tmp_path):
    """换模型 → 缓存 key 不同 → 重新调 API（不能跨模型串味）。"""
    calls = {"n": 0}

    def _fake_api(batch, **kwargs):
        calls["n"] += 1
        return [[0.1] for _ in batch]

    with patch.object(emb, "_embed_api", side_effect=_fake_api):
        emb.EmbeddingClient(cache_dir=tmp_path, model="m1").embed(["x"])
        emb.EmbeddingClient(cache_dir=tmp_path, model="m2").embed(["x"])
    assert calls["n"] == 2


# ---------------------------------------------------------------------------
# fail：网关失败抛错（由调用方降级，不静默吞）
# ---------------------------------------------------------------------------

def test_embed_gateway_failure_raises(tmp_path):
    def _boom(batch, **kwargs):
        raise RuntimeError("gateway 503")

    with patch.object(emb, "_embed_api", side_effect=_boom):
        client = emb.EmbeddingClient(cache_dir=tmp_path)
        with pytest.raises(Exception):
            client.embed(["anything"])


def test_embed_failure_does_not_poison_cache(tmp_path):
    """失败后不应把坏结果写进缓存；下次仍会重试。"""
    state = {"fail": True, "n": 0}

    def _api(batch, **kwargs):
        state["n"] += 1
        if state["fail"]:
            raise RuntimeError("transient")
        return [[0.5] for _ in batch]

    with patch.object(emb, "_embed_api", side_effect=_api):
        client = emb.EmbeddingClient(cache_dir=tmp_path)
        with pytest.raises(Exception):
            client.embed(["t"])
        state["fail"] = False
        out = client.embed(["t"])  # 重试应成功
    assert out == [[0.5]]
    assert state["n"] == 2


# ---------------------------------------------------------------------------
# 工具：cosine 相似度（scoring 语义维度会用）
# ---------------------------------------------------------------------------

def test_cosine_basic():
    assert emb.cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert emb.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert emb.cosine([0.0, 0.0], [1.0, 1.0]) == 0.0  # 零向量保护
