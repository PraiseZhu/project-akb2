"""Tests for 6-dimension repo scoring (v2)."""

import pytest

from akb2.briefing.github.scoring import (
    score,
    score_batch,
    mention_threshold,
    safety_check,
    mention_count_score,
    mentioner_authority_score,
    stars_score,
    activity_score,
    readme_quality_score,
    topic_relevance_score,
)


class TestDimensionScores:
    # --- mention_count (v2: denominator 12) ---
    def test_mention_count_normalized(self):
        assert mention_count_score(0) == 0.0
        assert mention_count_score(12) == 1.0
        assert mention_count_score(20) == 1.0  # capped at 1.0

    def test_mention_count_not_saturated_at_5(self):
        """SC-2: mention=5 should NOT be 1.0 (was 1.0 with /5, now /12)."""
        assert mention_count_score(5) == pytest.approx(5 / 12)  # ~0.417

    def test_mention_count_strictly_monotonic(self):
        """SC-2: mention=2/5/8/12 strictly increasing, no early saturation."""
        s2 = mention_count_score(2)
        s5 = mention_count_score(5)
        s8 = mention_count_score(8)
        s12 = mention_count_score(12)
        assert s2 < s5 < s8 < s12
        assert s2 > 0.0
        assert s5 > 0.0

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

    def test_mention_saturation_fix_affects_score(self):
        """SC-2: with denominator 12, mention=5 no longer saturates to 1.0
        → overall score lower than mention=12."""
        base = {
            "mentioners": ["@a", "@b"],
            "stars": 10000,
            "commits_7d": 15,
            "velocity_7d": None,
            "readme_excerpt": "x" * 1001 + "\n```py\nx\n```",
            "topic_tags": ["ai", "agent", "llm", "python", "rust", "cli"],
        }
        low = dict(base, mention_count=2)
        mid = dict(base, mention_count=5)
        high = dict(base, mention_count=12)

        s_low = score(low)
        s_mid = score(mid)
        s_high = score(high)
        assert s_low < s_mid < s_high

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
    def test_fixed_two(self):
        # 用户决策：门槛固定为 2（不再随活跃博主数自适应放大）
        assert mention_threshold(1) == 2
        assert mention_threshold(15) == 2
        assert mention_threshold(57) == 2   # 旧公式会算出 4
        assert mention_threshold(150) == 2  # 旧公式会算出 10