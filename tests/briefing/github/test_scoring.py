"""Tests for 6-dimension repo scoring (v2)."""

import pytest

from akb2.briefing.github import scoring
from akb2.briefing.github.scoring import (
    MULTI_MENTION_BONUS,
    activity_score,
    engagement_raw,
    engagement_score,
    mention_threshold,
    mentioner_authority_score,
    readme_quality_score,
    safety_check,
    score,
    score_batch,
    stars_score,
    topic_relevance_score,
)


class TestDimensionScores:
    # --- engagement (v3: tweet metrics, replaces mention_count in weighted sum) ---
    def test_engagement_none_is_zero(self):
        """Layer2/3 或缺失 metrics → 0（不崩、不中性）。"""
        assert engagement_score(None) == 0.0
        assert engagement_score({}) == 0.0
        assert engagement_raw(None) == 0

    def test_engagement_raw_weights_bookmarks_highest(self):
        """raw = likes + 2*retweets + 3*bookmarks；views 不算。"""
        m = {"likes": 10, "retweets": 5, "bookmarks": 2, "views": 999999}
        assert engagement_raw(m) == 10 + 2 * 5 + 3 * 2  # 26, views ignored

    def test_engagement_views_excluded(self):
        """views 高但其余为 0 → raw=0 → score=0（views 不该单独拉分）。"""
        assert engagement_raw({"views": 1_000_000}) == 0
        assert engagement_score({"views": 1_000_000}) == 0.0

    def test_engagement_log_normalized_monotonic(self):
        """log10 归一：raw 越大分越高，且不早饱和。"""
        s_low = engagement_score({"likes": 5})        # raw=5
        s_mid = engagement_score({"likes": 100})      # raw=100
        s_high = engagement_score({"likes": 1000})    # raw=1000
        s_max = engagement_score({"likes": 100000})   # raw=100000 → capped
        assert 0.0 < s_low < s_mid < s_high <= s_max == 1.0

    def test_engagement_bookmarks_beat_likes(self):
        """同样的原始数，收藏比点赞更值钱。"""
        s_likes = engagement_score({"likes": 30})
        s_bookmarks = engagement_score({"bookmarks": 30})
        assert s_bookmarks > s_likes

    def test_engagement_dirty_values_do_not_crash(self):
        """Codex P2: 上游 metrics 字段不稳(None/字符串/负数)时 coerce 为 0，
        不 raise TypeError 崩打分。脏值 = 0 而非中断（fail-safe）。"""
        assert engagement_raw({"likes": None, "retweets": None, "bookmarks": None}) == 0
        assert engagement_raw({"likes": "abc", "retweets": "x"}) == 0
        assert engagement_raw({"likes": -50}) == 0  # 负数 clamp 到 0
        # 数字字符串可被 int() 解析 → 正常计入
        assert engagement_raw({"likes": "30"}) == 30
        # 混合：脏的归 0，干净的正常
        assert engagement_raw({"likes": None, "bookmarks": 10}) == 30  # 3*10
        assert engagement_score({"likes": None}) == 0.0

    # --- mentioner_authority (v2: tier-based, decoupled from count) ---
    def test_mentioner_authority_with_tier(self):
        """SC-3: same mention count, different tier → different score."""
        tier_map = {"a": 1, "b": 2, "c": 3}
        # 3 core curators → max
        s_high = mentioner_authority_score(["a", "b", "c"], tier_map)
        # 3 tier-3 curators → lower
        s_low = mentioner_authority_score(["c", "c", "c"], tier_map)
        assert s_high > s_low
        assert s_high > 0.5

    def test_mentioner_authority_tier_decoupled_from_count(self):
        """SC-3: 5 mentions all tier-3 ≠ 5 mentions all tier-1."""
        tier_map = {"core": 1, "low": 3}
        # 5 core-tier mentions
        s_core = mentioner_authority_score(["core"] * 5, tier_map)
        # 5 low-tier mentions
        s_low = mentioner_authority_score(["low"] * 5, tier_map)
        assert s_core > s_low

    def test_mentioner_authority_empty(self):
        assert mentioner_authority_score([], {}) == 0.0
        assert mentioner_authority_score([], None) == 0.0

    def test_mentioner_authority_fallback_no_tier_map(self):
        """Without tier_map, falls back to len/5 proxy (backward compat)."""
        assert mentioner_authority_score(["a", "b", "c", "d", "e"]) == 1.0

    def test_mentioner_authority_strips_at(self):
        """Handles @-prefixed handles."""
        tier_map = {"karpathy": 1}
        s = mentioner_authority_score(["@karpathy"], tier_map)
        assert s > 0.0

    # --- stars (unchanged) ---
    def test_stars_log_scale(self):
        assert stars_score(1) == 0.0  # log10(1)/5 = 0
        assert stars_score(100000) == 1.0  # log10(100000)/5 = 5/5 = 1.0
        assert stars_score(0) == 0.0

    # --- activity_score (v2: replaces recency_score) ---
    def test_activity_velocity_priority(self):
        """velocity_7d takes priority over commits_7d."""
        s = activity_score(commits_7d=10, velocity_7d=100)
        assert s == 1.0  # velocity/50 = 100/50 = 2.0 → capped 1.0

    def test_activity_commits_fallback(self):
        """When velocity is None, uses commits_7d."""
        assert activity_score(commits_7d=15) == 0.5  # 15/30
        assert activity_score(commits_7d=30) == 1.0  # 30/30

    def test_activity_zero_commits_is_inactive(self):
        """SC-4: commits_7d=0 (truly inactive) → 0.0, not neutral."""
        assert activity_score(commits_7d=0) == 0.0

    def test_activity_none_is_neutral(self):
        """SC-4: commits_7d=None (unknown) → 0.5 neutral (NOT same as 0)."""
        assert activity_score(commits_7d=None) == 0.5

    def test_activity_both_none_is_neutral(self):
        assert activity_score() == 0.5
        assert activity_score(commits_7d=None, velocity_7d=None) == 0.5

    def test_activity_differentiates_active_vs_stale(self):
        """SC-4: active repo (commits_7d=25) > dead repo (commits_7d=0)."""
        s_active = activity_score(commits_7d=25)  # 25/30 ≈ 0.833
        s_dead = activity_score(commits_7d=0)     # 0.0
        assert s_active > s_dead

    # --- readme_quality (unchanged) ---
    def test_readme_quality_both_factors(self):
        long_with_code = "x" * 1001 + "\n```python\ncode\n```"
        assert readme_quality_score(long_with_code) == 1.0

    def test_readme_quality_none(self):
        assert readme_quality_score(None) == 0.0

    # --- topic_relevance (unchanged) ---
    def test_topic_relevance_matches(self):
        assert topic_relevance_score(["ai", "agent", "llm", "python", "rust", "cli"]) == 1.0
        assert topic_relevance_score(["crypto", "nft"]) == 0.0
        assert topic_relevance_score([]) == 0.0


class TestSafetyCheck:
    def test_safety_blocks_crypto_topic(self):
        assert not safety_check({"topic_tags": ["crypto", "ai"]})

    def test_safety_blocks_nsfw_readme(self):
        assert not safety_check({"readme_excerpt": "This is NSFW content", "topic_tags": []})

    def test_safety_passes_clean_repo(self):
        assert safety_check({
            "topic_tags": ["python", "ml"],
            "readme_excerpt": "A clean ML library",
            "repo_url": "https://github.com/owner/clean-repo",
        })


class TestScore:
    def test_full_score_computation(self):
        """SC-5: 6-dim weighted score, returns ∈ (0,1]."""
        data = {
            "mention_count": 3,
            "mentioners": ["@a", "@b", "@c"],
            "stars": 10000,
            "commits_7d": 15,
            "velocity_7d": None,
            "readme_excerpt": "x" * 501 + "\n```python\ncode\n```",
            "topic_tags": ["python", "ml", "ai", "agent", "cli", "rust"],
        }
        s = score(data)
        assert 0 < s <= 1.0

    def test_score_zero_on_safety_fail(self):
        data = {
            "mention_count": 10,
            "mentioners": ["@a"] * 10,
            "stars": 50000,
            "topic_tags": ["crypto"],
        }
        assert score(data) == 0.0

    def test_score_batch_sorts_descending(self):
        repos = [
            {"mention_count": 1, "mentioners": ["@a"], "stars": 100, "topic_tags": ["ai"]},
            {"mention_count": 5, "mentioners": ["@a", "@b", "@c", "@d", "@e"], "stars": 50000, "topic_tags": ["python", "ml", "ai", "agent", "cli", "rust"]},
            {"mention_count": 2, "mentioners": ["@a", "@b"], "stars": 1000, "topic_tags": ["python"]},
        ]
        scored = score_batch(repos)
        assert scored[0]["score"] >= scored[1]["score"] >= scored[2]["score"]

    def test_activity_now_used_in_scoring(self):
        """SC-4/SC-5: activity_score now replaces recency_score — verify it affects total."""
        data_active = {
            "mention_count": 3,
            "mentioners": ["@a", "@b", "@c"],
            "stars": 10000,
            "commits_7d": 30,
            "velocity_7d": None,
            "readme_excerpt": "x" * 500,
            "topic_tags": ["python"],
        }
        data_dead = dict(data_active, commits_7d=0)
        s_active = score(data_active)
        s_dead = score(data_dead)
        # Active repo should score higher than dead one (all else equal)
        assert s_active > s_dead

    def test_weights_sum_to_one(self):
        """v3: 6 个加权维之和必须 = 1.0（bonus 不算在内）。"""
        total = (
            scoring.W_MENTIONER_AUTHORITY
            + scoring.W_TOPIC_RELEVANCE
            + scoring.W_ENGAGEMENT
            + scoring.W_ACTIVITY
            + scoring.W_STARS
            + scoring.W_README_QUALITY
        )
        assert total == pytest.approx(1.0)

    def test_engagement_affects_score(self):
        """v3: engagement 维真正进入 total —— 高互动 repo 分更高（其余相同）。"""
        base = {
            "mention_count": 1,
            "mentioners": ["@a"],
            "stars": 10000,
            "commits_7d": 15,
            "velocity_7d": None,
            "readme_excerpt": "x" * 501,
            "topic_tags": ["python"],
        }
        hot = dict(base, engagement={"likes": 5000, "retweets": 800, "bookmarks": 1200})
        cold = dict(base, engagement=None)
        assert score(hot) > score(cold)

    def test_multi_mention_bonus_applied(self):
        """v3: mention_count>=2（独立共识）加 +0.08 bonus；==1 不加。"""
        base = {
            "mentioners": ["@a", "@b"],
            "stars": 5000,
            "commits_7d": 10,
            "velocity_7d": None,
            "readme_excerpt": "x" * 100,
            "topic_tags": ["python"],
            "engagement": None,
        }
        single = dict(base, mention_count=1)
        consensus = dict(base, mention_count=2)
        # 同样的维度分，consensus 因 bonus 高出约 MULTI_MENTION_BONUS
        assert score(consensus) - score(single) == pytest.approx(MULTI_MENTION_BONUS, abs=1e-4)

    def test_multi_mention_bonus_does_not_overflow(self):
        """bonus 后仍封顶 1.0。"""
        maxed = {
            "mention_count": 5,
            "mentioners": ["@a", "@b", "@c"],
            "stars": 100000,
            "commits_7d": 30,
            "velocity_7d": None,
            "readme_excerpt": "x" * 1001 + "\n```py\nx\n```",
            "topic_tags": ["ai", "agent", "llm", "python", "rust", "cli"],
            "engagement": {"likes": 50000, "retweets": 9000, "bookmarks": 9000},
            "_tier_map": {"a": 1, "b": 1, "c": 1},
        }
        assert score(maxed) <= 1.0

    def test_tier_affects_score(self):
        """SC-3: same mention count, different tiers → different total score."""
        base = {
            "mention_count": 3,
            "stars": 10000,
            "commits_7d": 15,
            "velocity_7d": None,
            "readme_excerpt": "x" * 1001 + "\n```py\nx\n```",
            "topic_tags": ["ai", "agent", "llm", "python", "rust", "cli"],
        }
        high_tier = dict(base, mentioners=["@karpathy", "@simonw", "@swyx"])
        high_tier["_tier_map"] = {"karpathy": 1, "simonw": 1, "swyx": 1}

        low_tier = dict(base, mentioners=["@GitHub_Daily", "@framer", "@codrops"])
        low_tier["_tier_map"] = {"GitHub_Daily": 3, "framer": 3, "codrops": 3}

        assert score(high_tier) > score(low_tier)


class TestDistributionFixture:
    """SC-6: Differentiated mock fixtures — each dimension has distinguishable impact."""

    @pytest.fixture
    def fixture_repos(self):
        import json
        from pathlib import Path
        path = Path(__file__).parent.parent.parent / "fixtures" / "briefing" / "github" / "scoring-distribution.json"
        with open(path) as f:
            return json.load(f)["repos"]

    def test_high_all_top(self, fixture_repos):
        """high-all should have the highest score."""
        scored = score_batch(fixture_repos)
        top = scored[0]
        assert top["repo_url"] == "https://github.com/org/high-all"

    def test_dead_repo_at_bottom(self, fixture_repos):
        """Dead repo (0 stars, 0 activity, no topics) should be last."""
        scored = score_batch(fixture_repos)
        bottom = scored[-1]
        assert bottom["repo_url"] == "https://github.com/org/dead-repo"

    def test_irrelevant_topics_ranked_below_relevant(self, fixture_repos):
        """Repo with no AI topics should rank lower than comparable relevant repo."""
        scored = score_batch(fixture_repos)
        no_readme_idx = None
        irrelevant_idx = None
        for i, r in enumerate(scored):
            if r["repo_url"] == "https://github.com/org/no-readme":
                no_readme_idx = i
            if r["repo_url"] == "https://github.com/org/irrelevant-topics":
                irrelevant_idx = i
        assert irrelevant_idx is not None
        assert no_readme_idx is not None
        # irrelevant-topics (0 topic_relevance) ranks below no-readme (max topic_relevance)
        assert irrelevant_idx > no_readme_idx

    def test_zero_activity_outranks_dead(self, fixture_repos):
        """zero-activity (commits_7d=0) > dead-repo (commits_7d=0 + 0 stars + no topics)."""
        scored = score_batch(fixture_repos)
        zero_act_idx = None
        dead_idx = None
        for i, r in enumerate(scored):
            if r["repo_url"] == "https://github.com/org/zero-activity":
                zero_act_idx = i
            if r["repo_url"] == "https://github.com/org/dead-repo":
                dead_idx = i
        assert zero_act_idx is not None
        assert dead_idx is not None
        assert zero_act_idx < dead_idx


class TestMentionThreshold:
    def test_fixed_one(self):
        # v3 用户决策（方案 A）：门槛降到 1（任何 1 个博主提及即够格进候选），
        # 共识改为 score 内 +0.08 bonus，不再当门槛。
        assert mention_threshold(1) == 1
        assert mention_threshold(15) == 1
        assert mention_threshold(57) == 1
        assert mention_threshold(150) == 1
