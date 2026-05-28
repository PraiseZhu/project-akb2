"""scoring.py 单元测试，覆盖 7 个维度边界 + 端到端测试。"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
FIXTURE_PATH = REPO_ROOT / "tests/fixtures/intake/dry-run-2026-05-27/sample.json"


# ---------------------------------------------------------------------------
# recency
# ---------------------------------------------------------------------------

def test_recency_fresh():
    from akb2.briefing.scoring import recency
    tweet = {"created_at": datetime.now(timezone.utc).isoformat()}
    assert recency(tweet) >= 0.9


def test_recency_24h():
    from akb2.briefing.scoring import recency
    t = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    score = recency({"created_at": t})
    assert 0.4 <= score <= 0.6


def test_recency_old():
    from akb2.briefing.scoring import recency
    t = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    assert recency({"created_at": t}) <= 0.15


# ---------------------------------------------------------------------------
# engagement
# ---------------------------------------------------------------------------

def test_engagement_zero():
    from akb2.briefing.scoring import engagement
    assert engagement({"metrics": {"likes": 0, "retweets": 0, "replies": 0}}) == 0.0


def test_engagement_high():
    from akb2.briefing.scoring import engagement
    score = engagement({"metrics": {"likes": 9999, "retweets": 0, "replies": 0}})
    assert score >= 0.8


# ---------------------------------------------------------------------------
# author_authority
# ---------------------------------------------------------------------------

def test_author_authority_whitelist():
    from akb2.briefing.scoring import author_authority
    assert author_authority({"author": "openai"}) == 1.0
    assert author_authority({"author": "@sama"}) == 1.0
    assert author_authority({"author": "@AnthropicAI"}) == 1.0
    assert author_authority({"author": "unknown_person"}) == 0.3


# ---------------------------------------------------------------------------
# topic_relevance
# ---------------------------------------------------------------------------

def test_topic_relevance_keywords():
    from akb2.briefing.scoring import topic_relevance
    tweet_hit = {"text": "New LLM model from AI lab shows GPT-level reasoning"}
    tweet_miss = {"text": "Good morning everyone"}
    assert topic_relevance(tweet_hit) > topic_relevance(tweet_miss)
    assert topic_relevance(tweet_miss) == 0.0


def test_topic_relevance_saturation():
    from akb2.briefing.scoring import topic_relevance
    tweet = {"text": "AI LLM AGI GPT Claude Llama DeepSeek transformer neural model agent multimodal reasoning"}
    assert topic_relevance(tweet) == 1.0


# ---------------------------------------------------------------------------
# safety
# ---------------------------------------------------------------------------

def test_safety_banned_keyword():
    from akb2.briefing.scoring import safety, ScoreEngine
    assert safety({"text": "buy this scam"}) == 0.0
    assert safety({"text": "AI research paper"}) == 1.0
    # safety=0 的推文总分应该 ≤ 0.3
    engine = ScoreEngine()
    result = engine.score_tweet(
        {
            "id": "1",
            "text": "buy this scam",
            "author": "x",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    assert result["score"] <= 0.3


# ---------------------------------------------------------------------------
# novelty
# ---------------------------------------------------------------------------

def test_novelty_dedup():
    from akb2.briefing.scoring import novelty
    seen: set = set()
    tweet = {"text": "Same text"}
    assert novelty(tweet, seen) == 1.0
    assert novelty(tweet, seen) == 0.0


def test_novelty_different_texts():
    from akb2.briefing.scoring import novelty
    seen: set = set()
    assert novelty({"text": "First tweet"}, seen) == 1.0
    assert novelty({"text": "Second tweet"}, seen) == 1.0


# ---------------------------------------------------------------------------
# signal_density
# ---------------------------------------------------------------------------

def test_signal_density_empty():
    from akb2.briefing.scoring import signal_density
    assert signal_density({"text": ""}) == 0.0


def test_signal_density_long_text():
    from akb2.briefing.scoring import signal_density
    long_text = "A" * 280
    score = signal_density({"text": long_text})
    assert score >= 0.5


# ---------------------------------------------------------------------------
# ScoreEngine & end-to-end
# ---------------------------------------------------------------------------

def test_score_clamped():
    """Score 始终在 [0, 1] 内。"""
    from akb2.briefing.scoring import ScoreEngine
    engine = ScoreEngine()
    tweet = {
        "id": "1",
        "text": "AI LLM model agent reasoning GPT Claude",
        "author": "@openai",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metrics": {"likes": 50000, "retweets": 20000, "replies": 5000},
    }
    result = engine.score_tweet(tweet)
    assert 0.0 <= result["score"] <= 1.0


def test_dimensions_keys_complete():
    """每条推文 dimensions 必须包含全部 7 个维度键。"""
    from akb2.briefing.scoring import ScoreEngine
    engine = ScoreEngine()
    tweet = {
        "id": "1",
        "text": "AI research update",
        "author": "@openai",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metrics": {"likes": 100, "retweets": 50, "replies": 10},
    }
    result = engine.score_tweet(tweet)
    expected_keys = sorted(["author_authority", "engagement", "novelty", "recency", "safety", "signal_density", "topic_relevance"])
    assert sorted(result["dimensions"].keys()) == expected_keys


def test_weights_json_changes_score():
    """--weights-json 有效改变 score。"""
    from akb2.briefing.scoring import ScoreEngine
    tweet = {
        "id": "1",
        "text": "AI research update",
        "author": "@sama",
        "created_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "metrics": {"likes": 1000, "retweets": 500, "replies": 100},
    }
    default_engine = ScoreEngine()
    recency_only_engine = ScoreEngine(
        weights={
            "recency": 1.0,
            "engagement": 0.0,
            "author_authority": 0.0,
            "topic_relevance": 0.0,
            "signal_density": 0.0,
            "novelty": 0.0,
            "safety": 0.0,
        }
    )
    score_default = default_engine.score_tweet(tweet)["score"]
    score_recency = recency_only_engine.score_tweet(tweet)["score"]
    assert score_default != score_recency


def test_end_to_end_with_step1_fixture():
    """端到端：读 step-1 fixture，断言 ≥1 条 score > 0.7 且 safety > 0。"""
    from akb2.briefing.scoring import ScoreEngine
    data = json.load(open(FIXTURE_PATH))
    engine = ScoreEngine()
    scored = [engine.score_tweet(t) for t in data["tweets"]]
    assert len(scored) > 0
    high_score = [t for t in scored if t["score"] > 0.7 and t["dimensions"]["safety"] > 0]
    assert len(high_score) > 0, (
        f"No tweet with score > 0.7 and safety > 0. "
        f"Scores: {[round(t['score'], 3) for t in scored]}"
    )
