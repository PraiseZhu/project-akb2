"""synthesis.py 单元测试。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
FIXTURE_PATH = REPO_ROOT / "tests/fixtures/briefing/scored-sample.json"


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


# ---------------------------------------------------------------------------
# SC-5b test 1: happy path — fallback 产出合规 payload
# ---------------------------------------------------------------------------

def test_happy_path_fallback():
    """无 API key 时 fallback 产出合规 payload。"""
    from akb2.briefing.synthesis import synthesize

    scored = _load_fixture()
    with patch("akb2.briefing.synthesis._has_api_key", return_value=False):
        result = synthesize(scored, date="2026-05-27")

    assert result["date"] == "2026-05-27"
    assert len(result["tweets"]) > 0
    assert len(result["focus_discussions"]) > 0
    # schema 校验
    from akb2.briefing.validator import validate_payload

    validate_payload(result)  # 不抛异常 = pass


# ---------------------------------------------------------------------------
# SC-5b test 2: schema reject — 缺字段的 payload 应该 raise
# ---------------------------------------------------------------------------

def test_schema_reject():
    """_validate_response 对缺字段的 payload raise ValidationError。"""
    import jsonschema

    from akb2.briefing.synthesis import _validate_response

    bad_payload = {"date": "2026-05-27", "tweets": []}  # 缺 required 字段
    with pytest.raises(jsonschema.ValidationError):
        _validate_response(bad_payload)


# ---------------------------------------------------------------------------
# SC-5b test 3: retry — _call_deepseek 首次失败，第二次成功
# ---------------------------------------------------------------------------

def test_retry_on_api_failure():
    """_call_deepseek 遇到网络错误时重试，第二次成功后返回内容。"""
    import httpx

    from akb2.briefing import synthesis

    call_count = [0]
    _valid_content = json.dumps(
        {
            "date": "2026-05-27",
            "generated_at": "2026-05-27T07:30:00+00:00",
            "source": {"name": "deepseek_synthesis", "collector_version": "1.0.0"},
            "scoring_summary": {"total_tweets": 1, "scored_tweets": 1},
            "tweets": [
                {
                    "id": "1",
                    "author": "@test",
                    "text": "AI test tweet",
                    "created_at": "2026-05-27T00:00:00Z",
                }
            ],
            "focus_discussions": [
                {
                    "topic": "AI Trends",
                    "supporting_tweet_ids": ["1"],
                    "summary": "Test summary citing AI benchmark data.",
                }
            ],
        }
    )

    def mock_post(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] < 2:
            raise httpx.ConnectError("connection refused")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": _valid_content}}]
        }
        mock_resp.raise_for_status.return_value = None
        return mock_resp

    with patch("httpx.post", side_effect=mock_post):
        with patch("time.sleep"):  # 跳过实际 sleep
            result = synthesis._call_deepseek("test prompt", max_retries=2)

    assert call_count[0] == 2  # 第一次失败，第二次成功
    data = json.loads(result)
    assert "focus_discussions" in data


# ---------------------------------------------------------------------------
# SC-5b test 4: fallback schema valid — _fallback_synthesis 产出必须通过 schema
# ---------------------------------------------------------------------------

def test_fallback_schema_valid():
    """fallback 产出必须通过 schema 校验。"""
    from akb2.briefing.synthesis import _fallback_synthesis
    from akb2.briefing.validator import validate_payload

    scored = _load_fixture()
    result = _fallback_synthesis(scored, date="2026-05-27")
    validate_payload(result)  # 不抛异常 = pass


def test_merge_with_input_includes_referenced_tweets_beyond_top10():
    """_merge_with_input 应确保 supporting_tweet_ids 引用的推文出现在 tweets 输出集里。"""
    from akb2.briefing.synthesis import _merge_with_input
    from akb2.briefing.audit import _check_standard

    # 11 条 scored tweets；model 引用第 11 名（idx=10）
    scored_input = {
        "tweets": [{"id": str(i), "text": f"tweet {i}", "author": f"@u{i}", "created_at": "", "score": 1.0 - i * 0.05, "url": ""} for i in range(1, 12)],
    }
    top = sorted(scored_input["tweets"], key=lambda t: t.get("score", 0), reverse=True)
    assert str(top[10]["id"]) == "11", "第11名应为 id=11"

    model_out = {
        "focus_discussions": [
            {"topic": "AI趋势讨论", "supporting_tweet_ids": ["11"], "summary": "这是第11条推文。"},
        ],
        "executive_summary": "执行摘要。",
    }
    merged = _merge_with_input(model_out, scored_input, "2026-05-30", top)

    # id=11 应在 tweets 输出中
    tweet_ids = [t["id"] for t in merged["tweets"]]
    assert "11" in tweet_ids, f"id=11 应在 tweets 输出中，实际 tweet_ids={tweet_ids}"

    # _check_standard 不应报 tweet_id 不存在（因为已补入 tweets）
    issues = _check_standard(merged)
    missing_issues = [i for i in issues if "不存在" in i and "11" in i]
    assert not missing_issues, f"audit 不应报 id=11 不存在，issues={missing_issues}"


# ---------------------------------------------------------------------------
# SC-5b test 5: CLI e2e — 产出文件且通过 validator
# ---------------------------------------------------------------------------

def test_synthesize_output_file():
    """CLI 运行产出文件且通过 validator。"""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "akb2.briefing.synthesis",
            "--input",
            str(FIXTURE_PATH),
            "--output",
            "/tmp/synth-test.json",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
    )
    assert result.returncode == 0, f"synthesis CLI failed:\n{result.stderr}"

    # validate 输出
    val_result = subprocess.run(
        [sys.executable, "-m", "akb2.briefing.validator", "/tmp/synth-test.json"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
    )
    assert val_result.returncode == 0, f"validator failed:\n{val_result.stderr}"
    assert "passes social_trends_v1_1" in val_result.stdout
