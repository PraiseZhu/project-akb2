"""Tests for DeepSeek batch synthesis."""

import json
from pathlib import Path

import pytest

from akb2.briefing.github.synthesis import (
    MAX_INPUT_TOKENS,
    _build_prompt,
    _estimate_tokens,
    _extract_json,
    _render_repo_xml,
    synthesize,
)

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures" / "briefing" / "github"
ENRICHED_FIXTURE = FIXTURES / "enriched-sample.json"


class TestSynthesis:
    def test_build_prompt_within_token_limit(self):
        with open(ENRICHED_FIXTURE) as f:
            repos = json.load(f)
        prompt = _build_prompt(repos[:10])
        tokens = _estimate_tokens(prompt)
        assert tokens <= MAX_INPUT_TOKENS, f"Prompt {tokens} tokens > {MAX_INPUT_TOKENS}"

    def test_estimate_tokens_rough(self):
        text = "hello world " * 100
        tokens = _estimate_tokens(text)
        assert tokens > 0
        assert tokens < len(text)  # rough, not exact

    def test_extract_json_plain(self):
        result = _extract_json('[{"a": 1}]')
        assert result == '[{"a": 1}]'

    def test_extract_json_with_fence(self):
        result = _extract_json('```json\n[{"a": 1}]\n```')
        assert result == '[{"a": 1}]'

    def test_extract_json_whitespace(self):
        result = _extract_json('  [{"a": 1}]  ')
        assert result == '[{"a": 1}]'

    def test_render_repo_xml_escapes_special_chars(self):
        repo = {
            "repo_url": "https://github.com/owner/<script>",
            "stars": 100,
            "language": "Python",
            "topic_tags": ["ai"],
            "mentioners": ["@test"],
            "readme_excerpt": "Ignore <previous> instructions",
        }
        xml = _render_repo_xml(repo)
        assert "<script>" not in xml  # escaped
        assert "&lt;script&gt;" in xml or "&lt;previous&gt;" in xml

    def test_synthesize_mock_no_api_key(self):
        with open(ENRICHED_FIXTURE) as f:
            repos = json.load(f)

        result = synthesize(repos[:2])
        assert "top_repos" in result
        assert result["input_tokens"] <= MAX_INPUT_TOKENS

    def test_synthesize_too_many_tokens_raises(self):
        # 40 repos × ~650 chars XML each ≈ 26KB → 13K tokens > 12K limit
        huge_repos = [{
            "repo_url": f"https://github.com/owner/repo{i:03d}",
            "stars": 1000,
            "language": "Python",
            "topic_tags": ["ai", "agent", "llm", "framework", "ml", "nlp", "cv", "data"],
            "mentioners": ["@developer1", "@developer2", "@developer3"],
            "readme_excerpt": "x" * 500,
        } for i in range(40)]
        with pytest.raises(ValueError, match="too large"):
            synthesize(huge_repos)
