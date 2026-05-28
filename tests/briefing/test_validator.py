"""pytest 测试：akb2.briefing.validator — social_trends_v1 schema 校验。"""
import json
from pathlib import Path

import jsonschema
import pytest

from akb2.briefing.validator import validate_payload, validate_tweet

FIXTURES = Path(__file__).parent.parent / "fixtures" / "briefing"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class TestValidatePayload:
    def test_valid_payload(self):
        """valid sample 应通过 validate_payload 无异常。"""
        payload = _load("schema-valid-sample.json")
        validate_payload(payload)  # must not raise

    def test_missing_focus_raises(self):
        """缺少 focus_discussions 字段应 raise ValidationError。"""
        payload = _load("schema-invalid-missing-focus.json")
        with pytest.raises(jsonschema.ValidationError):
            validate_payload(payload)

    def test_wrong_type_raises(self):
        """tweets[0].id 为 int 而非 string，应 raise ValidationError，错误信息含 'tweets' 和 'id'。"""
        payload = _load("schema-invalid-wrong-type.json")
        with pytest.raises(jsonschema.ValidationError) as exc_info:
            validate_payload(payload)
        msg = exc_info.value.message
        assert "tweets" in msg or "id" in msg, f"错误信息未提及 tweets/id: {msg}"


class TestValidateTweet:
    def test_validate_tweet_valid(self):
        """从 valid sample 取第一条 tweet，validate_tweet 不抛异常。"""
        payload = _load("schema-valid-sample.json")
        tweet = payload["tweets"][0]
        validate_tweet(tweet)  # must not raise

    def test_validate_tweet_missing_id_raises(self):
        """缺 id 的 tweet dict，validate_tweet 应 raise ValidationError。"""
        tweet = {"text": "no id here", "author": "x", "created_at": "2026-05-27T00:00:00Z"}
        with pytest.raises(jsonschema.ValidationError):
            validate_tweet(tweet)

    def test_validate_tweet_missing_created_at_raises(self):
        """缺 created_at 的 tweet dict，validate_tweet 应 raise ValidationError。"""
        tweet = {"id": "1", "text": "no created_at", "author": "x"}
        with pytest.raises(jsonschema.ValidationError):
            validate_tweet(tweet)
