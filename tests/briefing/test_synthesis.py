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
    from akb2.briefing.audit import _check_standard
    from akb2.briefing.synthesis import _merge_with_input

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


# ---------------------------------------------------------------------------
# 质量门控：summary 空 / topic 非中文 触发重试
# ---------------------------------------------------------------------------

def test_validate_quality_empty_summary_raises():
    """focus_discussions 有空 summary → _validate_quality raise（触发合成重试）。"""
    from akb2.briefing.synthesis import _validate_quality

    merged = {
        "focus_discussions": [
            {"topic": "智能体长记忆", "summary": "完整的中文总结内容。", "supporting_tweet_ids": ["1"]},
            {"topic": "开源权重模型", "summary": "", "supporting_tweet_ids": ["2"]},  # 空
        ],
    }
    with pytest.raises(ValueError, match="summary 为空"):
        _validate_quality(merged)


def test_validate_quality_english_topics_pass():
    """topic 英文也通过（合成语言无关，中文化交 translate）——只要 summary 非空。"""
    from akb2.briefing.synthesis import _validate_quality

    merged = {
        "focus_discussions": [
            {"topic": "Agent memory infrastructure", "summary": "Real content.", "supporting_tweet_ids": ["1"]},
            {"topic": "Open weight models", "summary": "More content.", "supporting_tweet_ids": ["2"]},
        ],
    }
    _validate_quality(merged)  # 不抛 = pass


def test_validate_quality_banned_topic_raises():
    """空洞词 topic（如 AI Trends）→ raise。"""
    from akb2.briefing.synthesis import _validate_quality

    merged = {"focus_discussions": [{"topic": "AI Trends", "summary": "x.", "supporting_tweet_ids": ["1"]}]}
    with pytest.raises(ValueError, match="空洞词"):
        _validate_quality(merged)


def test_validate_quality_passes_good():
    """中文 topic + 非空 summary → 不 raise。"""
    from akb2.briefing.synthesis import _validate_quality

    merged = {
        "focus_discussions": [
            {"topic": "智能体长记忆基础设施", "summary": "第一句。第二句。第三句。", "supporting_tweet_ids": ["1"]},
        ],
    }
    _validate_quality(merged)  # 不抛 = pass


def test_patch_focus_discussions_filters_fabricated_id():
    """SC-4：模型编造的 supporting_tweet_id 被过滤、真实 id 保留（非全编造，不走 top[:5] 兜底）。"""
    from akb2.briefing.synthesis import _patch_focus_discussions

    top = [{"id": "1"}, {"id": "2"}, {"id": "3"}]
    discs = [{"topic": "T", "summary": "第一句。第二句。第三句。", "supporting_tweet_ids": ["1", "FAKE999"]}]

    patched = _patch_focus_discussions(discs, top)
    ids = patched[0]["supporting_tweet_ids"]

    assert "FAKE999" not in ids, "编造 id 必须被过滤掉"
    assert "1" in ids, "真实 id 必须保留"
    assert all(i in {"1", "2", "3"} for i in ids), "结果只能含真实 top 推文 id"


def test_synthesize_retries_on_degraded_then_succeeds():
    """合成首次退化(summary空)→重试→第二次正常，最终返回好结果。"""
    import json as _json
    from unittest.mock import patch

    from akb2.briefing import synthesis as syn

    scored = _load_fixture()
    top = sorted(scored["tweets"], key=lambda t: t.get("score", 0), reverse=True)
    ids = [str(t["id"]) for t in top[:3]]

    degraded = _json.dumps({"focus_discussions": [{"topic": "退化主题", "supporting_tweet_ids": ids}]})  # 无 summary
    good = _json.dumps({
        "executive_summary": "今日要点综述。",
        "focus_discussions": [
            {"topic": "智能体长记忆基础设施", "summary": "第一句。第二句。第三句。", "supporting_tweet_ids": ids},
        ],
    })
    responses = [degraded, good]

    def fake_call(prompt, max_retries=2):
        return responses.pop(0)

    with patch.object(syn, "_has_api_key", return_value=True), \
         patch.object(syn, "_call_deepseek", side_effect=fake_call):
        r = syn.synthesize(scored, date="2026-05-30")

    fds = r.get("focus_discussions", [])
    assert fds and fds[0]["summary"].strip(), "重试后应拿到非空 summary"
    assert fds[0]["topic"] == "智能体长记忆基础设施"
    assert not responses, "应消耗掉退化响应、用第二次"


# ---------------------------------------------------------------------------
# P0: 短序号映射 — _build_indexed_tweet_listing + _map_index_refs_to_ids
# ---------------------------------------------------------------------------

def _make_top(n: int) -> list[dict]:
    return [{"id": f"19{i:017d}", "author": f"@u{i}", "text": f"tweet {i}", "score": 0.9 - i * 0.01} for i in range(1, n + 1)]


def test_build_indexed_tweet_listing_uses_short_indices():
    """_build_indexed_tweet_listing 输出 [1] [2]... 而非 19 位 raw ID。"""
    from akb2.briefing.synthesis import _build_indexed_tweet_listing

    top = _make_top(3)
    text, idx_to_id = _build_indexed_tweet_listing(top)

    assert "[1]" in text and "[2]" in text and "[3]" in text, "列表应含短序号 [1] [2] [3]"
    assert top[0]["id"] not in text, "19 位真实 ID 不应出现在列表中"
    assert idx_to_id == {"1": top[0]["id"], "2": top[1]["id"], "3": top[2]["id"]}


def test_map_index_refs_returns_real_ids():
    """_map_index_refs_to_ids 将模型返回的序号映射回真实 tweet id。"""
    from akb2.briefing.synthesis import _map_index_refs_to_ids

    idx_to_id = {"1": "AAA", "2": "BBB", "3": "CCC"}
    result = _map_index_refs_to_ids(["1", "3"], idx_to_id)
    assert result == ["AAA", "CCC"]


def test_map_index_refs_handles_bracket_format():
    """模型可能返回 '[1]' 带括号形式，应同样映射。"""
    from akb2.briefing.synthesis import _map_index_refs_to_ids

    idx_to_id = {"1": "AAA", "2": "BBB"}
    assert _map_index_refs_to_ids(["[1]", "[2]"], idx_to_id) == ["AAA", "BBB"]


def test_map_index_refs_drops_oob_and_unknown():
    """越界序号（超过推文总数）和非法值都被丢弃，不崩溃。"""
    from akb2.briefing.synthesis import _map_index_refs_to_ids

    idx_to_id = {"1": "AAA", "2": "BBB"}
    result = _map_index_refs_to_ids(["1", "99", "abc", "FAKE_ID_12345678901"], idx_to_id)
    assert result == ["AAA"], f"只保留有效序号，实际: {result}"


def test_synthesis_prompt_path_resolves_to_real_file():
    """P0 实跑根因回归：synthesize 的 prompt 路径必须指向真实存在的 prompt 文件。

    路径 off-by-one（多一层 parent）→ 文件找不到 → 回退内联兜底 prompt（无短序号
    指令、无 supporting_tweet_ids 格式说明）→ 模型乱返回 → 映射空 → 4 主题全塌 top[:5]。
    """
    from akb2.briefing import synthesis as syn

    assert syn._PROMPT_PATH.exists(), \
        f"合成 prompt 文件不存在（路径算错？）: {syn._PROMPT_PATH}"
    content = syn._PROMPT_PATH.read_text()
    assert "短序号" in content, "prompt 应包含短序号指令（确认加载的是正确文件，非内联兜底）"


def test_map_index_refs_accepts_real_ids_when_model_ignores_indices():
    """P0 实跑回归：gemini 不稳定，有时直接返回真实 19 位 id 而非短序号。

    映射必须同时接受两种格式——短序号翻译、真实 id 原样保留——否则真实 id 被全部
    丢弃 → 每个主题兜底 top[:5] → 4 主题挂相同推文（真跑暴露的 77% 冗余）。
    """
    from akb2.briefing.synthesis import _map_index_refs_to_ids

    idx_to_id = {"1": "1925001000000000012", "2": "1925001000000000007"}
    valid_ids = {"1925001000000000012", "1925001000000000007", "1925001000000000099"}

    # 模型直接返回真实 id（没听短序号指令）→ 应保留，不丢弃
    assert _map_index_refs_to_ids(["1925001000000000099"], idx_to_id, valid_ids) == \
        ["1925001000000000099"], "真实 id 应被保留"

    # 短序号 + 真实 id 混合 → 都保留
    assert _map_index_refs_to_ids(["1", "1925001000000000099"], idx_to_id, valid_ids) == \
        ["1925001000000000012", "1925001000000000099"], "短序号和真实 id 混合都应处理"

    # 既非短序号也非真实 id（编造）→ 丢弃
    assert _map_index_refs_to_ids(["FAKE999"], idx_to_id, valid_ids) == [], "编造 id 应丢弃"


def test_merge_keeps_distinct_ids_when_model_returns_real_ids():
    """P0 实跑回归：模型对各主题返回不同的真实 id → 合并后主题 ids 仍 distinct，不塌 top5。"""
    from akb2.briefing.synthesis import _build_indexed_tweet_listing, _merge_with_input

    scored = _load_fixture()
    top = sorted(scored["tweets"], key=lambda t: t.get("score", 0), reverse=True)
    _, idx_to_id = _build_indexed_tweet_listing(top)

    # 模型对 3 个主题返回不同的真实 19 位 id（模拟 gemini 不用短序号那次）
    real_ids = [str(t["id"]) for t in top[:6]]
    model_out = {
        "focus_discussions": [
            {"topic": "A", "summary": "一。二。三。", "supporting_tweet_ids": [real_ids[0], real_ids[1]]},
            {"topic": "B", "summary": "一。二。三。", "supporting_tweet_ids": [real_ids[2], real_ids[3]]},
            {"topic": "C", "summary": "一。二。三。", "supporting_tweet_ids": [real_ids[4], real_ids[5]]},
        ]
    }
    merged = _merge_with_input(model_out, scored, "2026-05-31", top, idx_to_id)
    id_sets = [tuple(fd["supporting_tweet_ids"]) for fd in merged["focus_discussions"]]
    assert len(set(id_sets)) == 3, f"3 主题应有 3 组不同 ids，实际塌成 {len(set(id_sets))} 组: {id_sets}"


def test_patch_no_fallback_when_indices_map_to_valid_ids():
    """P0 核心：模型用短序号 → 映射为真实 id → _patch_focus_discussions 不触发 top[:5] 兜底。"""
    from akb2.briefing.synthesis import _map_index_refs_to_ids, _patch_focus_discussions

    top = _make_top(8)
    idx_to_id = {str(i + 1): top[i]["id"] for i in range(len(top))}

    # 模拟模型返回短序号引用推文 1、4、7
    raw_model_ids = ["1", "4", "7"]
    mapped_ids = _map_index_refs_to_ids(raw_model_ids, idx_to_id)

    discs = [{"topic": "智能体记忆", "summary": "第一句。第二句。第三句。", "supporting_tweet_ids": mapped_ids}]
    patched = _patch_focus_discussions(discs, top)

    result_ids = set(patched[0]["supporting_tweet_ids"])
    # 映射后是真实 ID，patch 不应替换为 top[:5]
    assert top[0]["id"] not in result_ids or len(result_ids) >= 2, "映射成功时不应全部替换为 top[:5]"
    assert top[0]["id"] in patched[0]["supporting_tweet_ids"] or top[3]["id"] in patched[0]["supporting_tweet_ids"], \
        "应保留序号 1/4/7 对应的真实推文"


def test_synthesize_prompt_contains_indexed_listing(tmp_path):
    """synthesize() 调用 LLM 时 prompt 使用 [1] [2] 短序号而非 19 位 ID。"""
    import json as _json

    import akb2.briefing.synthesis as syn

    scored = _load_fixture()
    captured_prompts: list[str] = []

    def fake_call(prompt: str) -> str:
        captured_prompts.append(prompt)
        top = sorted(scored["tweets"], key=lambda t: t.get("score", 0), reverse=True)
        ids = [str(t["id"]) for t in top[:2]]
        return _json.dumps({
            "focus_discussions": [
                {"topic": "智能体长记忆基础设施", "summary": "第一句。第二句。第三句。", "supporting_tweet_ids": ["1", "2"]},
            ],
            "executive_summary": "今日摘要。",
        })

    with patch.object(syn, "_has_api_key", return_value=True), \
         patch.object(syn, "_call_deepseek", side_effect=fake_call):
        syn.synthesize(scored, date="2026-05-31")

    assert captured_prompts, "应调用 _call_deepseek"
    prompt = captured_prompts[0]
    assert "[1]" in prompt, "prompt 应含短序号 [1]"
    # 确认 19 位原始 ID 不在 prompt 里（防止模型被迷惑）
    top = sorted(scored["tweets"], key=lambda t: t.get("score", 0), reverse=True)
    assert top[0]["id"] not in prompt, "prompt 不应含 19 位 tweet ID"
