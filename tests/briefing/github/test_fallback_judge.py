"""Tests for fallback_judge — three-state decision: Layer1 OK / fail→Layer2 / fail→Layer3 / skip."""

import json
from unittest import mock

from akb2.briefing.github.intake import _ZERO_RECENCY_STATS, Candidate, CollectorResult
from akb2.briefing.github.intake.fallback_judge import FallbackDecision, decide


def _make_l1_result(ok=True, num_candidates=10, num_failed=5):
    candidates = [
        Candidate(f"https://github.com/test/repo{i}", "@test", "123",
                  "2026-05-28T12:00:00Z", "test tweet")
        for i in range(num_candidates)
    ]
    failed = [f"@fail{i}" for i in range(num_failed)]
    return CollectorResult("layer1", candidates, failed, 60.0, ok,
                           None if ok else "health check failed",
                           _ZERO_RECENCY_STATS)


def _make_l2_result(ok=True, num_candidates=12):
    candidates = [
        Candidate(f"https://github.com/trending/repo{i}", "@github_trending", None,
                  "2026-05-28T12:00:00Z", None)
        for i in range(num_candidates)
    ]
    return CollectorResult("layer2", candidates, [], 5.0, ok,
                           None if ok else "HTTP error",
                           _ZERO_RECENCY_STATS)


class TestFallbackJudge:
    def test_layer1_ok_selects_layer1(self):
        l1 = _make_l1_result(ok=True, num_candidates=10)
        l2_runner = mock.Mock()

        decision = decide(l1, l2_runner)

        assert decision.selected_layer == "layer1"
        assert len(decision.candidates) == 10
        l2_runner.assert_not_called()

    def test_layer1_few_candidates_falls_to_layer2(self):
        l1 = _make_l1_result(ok=True, num_candidates=2)  # < 5
        l2 = _make_l2_result(ok=True, num_candidates=12)

        decision = decide(l1, lambda: l2)

        assert decision.selected_layer == "layer2"
        assert len(decision.candidates) == 12

    def test_layer1_not_ok_falls_to_layer2(self):
        l1 = _make_l1_result(ok=False, num_candidates=0)
        l2 = _make_l2_result(ok=True, num_candidates=12)

        decision = decide(l1, lambda: l2)

        assert decision.selected_layer == "layer2"

    def test_layer1_and_layer2_fail_falls_to_layer3(self, tmp_path):
        l1 = _make_l1_result(ok=False, num_candidates=0)
        l2 = _make_l2_result(ok=False, num_candidates=5)

        manual = tmp_path / "2026-05-28.json"
        manual.write_text(json.dumps([
            {
                "repo_url": f"https://github.com/manual/repo{i}",
                "mentioner": "@admin",
                "mentioned_at": "2026-05-28T12:00:00Z",
            }
            for i in range(10)
        ]))

        decision = decide(l1, lambda: l2, layer3_path=manual)

        assert decision.selected_layer == "layer3"
        assert len(decision.candidates) == 10

    def test_all_layers_fail_skips(self, tmp_path):
        l1 = _make_l1_result(ok=False, num_candidates=0)
        l2 = _make_l2_result(ok=False, num_candidates=0)

        manual = tmp_path / "2026-05-28.json"
        manual.write_text("{}")  # empty, no candidates

        decision = decide(l1, lambda: l2, layer3_path=manual)

        assert decision.selected_layer == "skip"
        assert len(decision.candidates) == 0

    def test_layer2_too_few_repos_falls_to_layer3(self, tmp_path):
        l1 = _make_l1_result(ok=False, num_candidates=0)
        l2 = _make_l2_result(ok=True, num_candidates=8)  # < 10

        manual = tmp_path / "2026-05-28.json"
        manual.write_text(json.dumps([
            {"repo_url": f"https://github.com/fallback/repo{i}", "mentioner": "@admin",
             "mentioned_at": "2026-05-28T12:00:00Z"}
            for i in range(10)
        ]))

        decision = decide(l1, lambda: l2, layer3_path=manual)

        assert decision.selected_layer == "layer3"

    def test_fallback_decision_namedtuple(self):
        decision = FallbackDecision("layer1", "OK", [])
        assert decision.selected_layer == "layer1"
        assert decision.reason == "OK"
        assert decision.candidates == []
