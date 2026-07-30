"""test_f54_json_schema_degrade.py — F54 入库断流三刀（质量判定标准零动）。

生产故障实录（2026-07-30 诊断，账本实锤）：
  上游 2026-07-23T03:24Z 下掉 OpenAI 风格 json_schema。生产网关
  https://llm-proxy.tapsvc.com/v1 + deepseek/deepseek-v4-pro 实测:
      json_schema → 400 "This response_format type is unavailable now"
      json_object → 通
  准入四判定(topic_relevance/how_tendency/novelty/content_lane)全走
  route_llm(response_schema=...) → 全 400 → fail-closed 全拒 →
  2026-07-24T17:40Z 起连续 6 天 finalize=0（零入库）。
  且 abort 落账 reason 写成"准入未通过: topic_relevance, how_tendency, novelty,
  content_lane"——上游 API 故障被伪装成内容质量拒绝，7 天无人察觉（违宪法 P4）。
  F39 自动重审的 `_INFRA_ERROR_MARKERS` 只认宕机/超时，不含 400 类 → 这批
  infra 死亡永不重审。

三刀:
  刀1 (SC1) llm_router deepseek 族静态降级 json_object + schema 进 prompt +
           本地 jsonschema 校验；gpt-5.4/gemini 族一字不动。
  刀2 (SC2) _INFRA_ERROR_MARKERS 补 400/BadRequest/response_format 类指纹，
           使真实 abort 文本命中 → F39 自动重审恰好一次（预算 1 不变）。
  刀3 (SC3) abort 落账带 failure_kind: infra|content，API 故障不再伪装成内容拒绝。
"""

from __future__ import annotations

import json
import os
import types
from pathlib import Path
from unittest import mock

import pytest

from src.akb2.quality import runner
from src.akb2.quality.admission import AdmissionCheck
from src.akb2.quality.llm_router import (
    SCHEMA_PROMPT_HEADER,
    LLMProviderError,
    build_outbound_payload,
    build_schema_prompt_messages,
    route_llm,
    validate_response_against_schema,
)
from src.akb2.quality.runner import (
    AUTO_INFRA_REQUEUE_MAX,
    FAILURE_KIND_CONTENT,
    FAILURE_KIND_INFRA,
    _admission_failure_probe,
    _classify_failure_kind,
    _is_infra_error,
    _maybe_auto_infra_requeue,
)

# ---------------------------------------------------------------------------
# 真机故障原文（2026-07-30 实测，SC2/SC3 断言以此为准）
# ---------------------------------------------------------------------------

# 生产网关逐字原文（2026-07-30 探针实取，_tmp/debug/f54_probe.py 形态 A）：
#   openai.BadRequestError → route_llm 包成 LLMProviderError("LLM API 错误: ...")
#   → admission 各 check 包成 "判定调用失败: ..." 写进 AdmissionCheck.reason
GATEWAY_400_RAW = (
    "Error code: 400 - {'error': {'message': 'litellm.BadRequestError: "
    'DeepseekException - {"error":{"message":"This response_format type is '
    'unavailable now","type":"invalid_request_error","param":null,'
    '"code":"invalid_request_error"}}\', \'type\': None, \'param\': None, '
    "'code': '400'}}"
)
REAL_CHECK_REASON_400 = (
    f"判定调用失败: LLM API 错误: deepseek-v4-pro — {GATEWAY_400_RAW}"
)
# 准入整体 reason（旧账本唯一可见的文本——不含任何 infra 信号，正是漏检根因）
REAL_ADMISSION_REASON = (
    "准入未通过: topic_relevance, how_tendency, novelty, content_lane"
)

_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "pass": {"type": "boolean"},
        "reason": {"type": "string", "maxLength": 300},
    },
    "required": ["pass", "reason"],
    "additionalProperties": False,
}


def _fake_openai(response_text: str, captured: dict) -> types.SimpleNamespace:
    """最小 openai 替身：捕获出站 kwargs，回放指定响应文本。"""

    class _FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            message = types.SimpleNamespace(content=response_text)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=_FakeCompletions())

    return types.SimpleNamespace(
        OpenAI=_FakeOpenAI,
        APITimeoutError=TimeoutError,
        APIError=RuntimeError,
    )


# ---------------------------------------------------------------------------
# 刀1 / SC1: deepseek 族 json_object 静态降级
# ---------------------------------------------------------------------------


class TestDeepseekJsonObjectDegrade:
    """SC1: deepseek 族 response_schema 请求不再发 json_schema。"""

    def test_deepseek_payload_has_no_json_schema(self):
        """核心回归锚：deepseek 族 payload 绝不含 json_schema（撞 400 的那个形态）。"""
        payload = build_outbound_payload(
            "deepseek-v4-pro",
            [{"role": "user", "content": "内容"}],
            response_schema=_CHECK_SCHEMA,
        )
        assert payload["response_format"] == {"type": "json_object"}
        assert "json_schema" not in json.dumps(payload, ensure_ascii=False)

    def test_deepseek_payload_carries_schema_in_prompt(self):
        """schema 约束必须进 prompt——端点不再强制 schema，prompt 是唯一约束来源。"""
        payload = build_outbound_payload(
            "deepseek-v4-pro",
            [{"role": "user", "content": "内容"}],
            response_schema=_CHECK_SCHEMA,
        )
        prompt_blob = json.dumps(payload["messages"], ensure_ascii=False)
        assert SCHEMA_PROMPT_HEADER in prompt_blob
        # schema 的字段名/枚举约束逐项在 prompt 里可见（不是只写"请输出 JSON"）
        for token in ("additionalProperties", "required", "maxLength", "pass", "reason"):
            assert token in prompt_blob, token
        # 原始消息不被丢弃
        assert payload["messages"][0] == {"role": "user", "content": "内容"}

    def test_prompt_mentions_json_for_deepseek_json_output_mode(self):
        """DeepSeek JSON Output 模式要求 prompt 含 json 提示词，否则端点拒绝。"""
        messages = build_schema_prompt_messages(
            [{"role": "user", "content": "内容"}], _CHECK_SCHEMA
        )
        assert "JSON" in messages[-1]["content"]

    def test_build_schema_prompt_messages_does_not_mutate_input(self):
        """重试会复用同一 messages —— 原地 append 会累积多份 schema 指令。"""
        original = [{"role": "user", "content": "内容"}]
        out = build_schema_prompt_messages(original, _CHECK_SCHEMA)
        assert original == [{"role": "user", "content": "内容"}]
        assert len(out) == 2

    def test_other_families_keep_json_schema(self):
        """gpt-5.4 / gemini 族路径一字不动（它们的 json_schema 仍可用）。"""
        for family in ("gpt-5.4", "gemini-flash"):
            payload = build_outbound_payload(
                family,
                [{"role": "user", "content": "内容"}],
                response_schema=_CHECK_SCHEMA,
            )
            assert payload["response_format"]["type"] == "json_schema", family
            assert payload["response_format"]["json_schema"]["strict"] is True, family
            # 非降级族不往 prompt 里塞 schema
            assert payload["messages"] == [{"role": "user", "content": "内容"}], family

    @mock.patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "gw-test-key"}, clear=True)
    def test_route_llm_sends_json_object_and_validates(self, monkeypatch):
        """route_llm 实发请求形态 = json_object + schema 进 prompt，响应经本地校验。"""
        captured: dict = {}
        monkeypatch.setitem(
            __import__("sys").modules,
            "openai",
            _fake_openai('{"pass": true, "reason": "命中 AI Agent 锚点"}', captured),
        )
        messages = [{"role": "user", "content": "内容"}]

        result = route_llm("deepseek-v4-pro", messages, response_schema=_CHECK_SCHEMA)

        assert captured["response_format"] == {"type": "json_object"}
        assert "json_schema" not in json.dumps(captured["response_format"])
        assert SCHEMA_PROMPT_HEADER in captured["messages"][-1]["content"]
        assert messages == [{"role": "user", "content": "内容"}]  # 调用方 messages 未被改
        assert result.content == {"pass": True, "reason": "命中 AI Agent 锚点"}

    @mock.patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "gw-test-key"}, clear=True)
    def test_route_llm_rejects_schema_violating_response(self, monkeypatch):
        """SC1 校验失败路径：端点不再强制 schema → 违规响应必须被本地校验拦下。"""
        captured: dict = {}
        monkeypatch.setitem(
            __import__("sys").modules,
            "openai",
            # pass 类型错（str 而非 bool）+ 缺 reason：旧 strict json_schema 下不可能出现
            _fake_openai('{"pass": "yes"}', captured),
        )
        with pytest.raises(LLMProviderError, match="本地 schema 校验失败"):
            route_llm(
                "deepseek-v4-pro",
                [{"role": "user", "content": "内容"}],
                response_schema=_CHECK_SCHEMA,
            )

    @mock.patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "gw-test-key"}, clear=True)
    def test_route_llm_rejects_non_json_response_on_degraded_path(self, monkeypatch):
        """降级路径语法失败也显式抛（宪法 P4），不静默回原文让下游猜。"""
        captured: dict = {}
        monkeypatch.setitem(
            __import__("sys").modules,
            "openai",
            _fake_openai("这不是 JSON", captured),
        )
        with pytest.raises(LLMProviderError, match="不是合法 JSON"):
            route_llm(
                "deepseek-v4-pro",
                [{"role": "user", "content": "内容"}],
                response_schema=_CHECK_SCHEMA,
            )

    @mock.patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "gw-test-key"}, clear=True)
    def test_non_degraded_family_keeps_raw_fallback_behaviour(self, monkeypatch):
        """gpt-5.4 非 JSON 响应仍回原始字符串（旧行为不动，避免连带改判）。"""
        captured: dict = {}
        monkeypatch.setitem(
            __import__("sys").modules,
            "openai",
            _fake_openai("这不是 JSON", captured),
        )
        result = route_llm(
            "gpt-5.4",
            [{"role": "user", "content": "内容"}],
            response_schema=_CHECK_SCHEMA,
        )
        assert result.content == "这不是 JSON"

    def test_validator_accepts_conforming_payload(self):
        validate_response_against_schema({"pass": False, "reason": "无锚点命中"}, _CHECK_SCHEMA)

    def test_validator_rejects_extra_field(self):
        """additionalProperties=False 仍被真执行（降级后靠本地校验守住）。"""
        with pytest.raises(LLMProviderError, match="本地 schema 校验失败"):
            validate_response_against_schema(
                {"pass": True, "reason": "ok", "score": 0.9}, _CHECK_SCHEMA
            )


# ---------------------------------------------------------------------------
# 刀2 / SC2: 400 类指纹进 F39，真实 abort 文本自动重审一次
# ---------------------------------------------------------------------------


def _mk_config(tmp_path: Path):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("", encoding="utf-8")

    class _Cfg:
        ledger_path = str(ledger)

    return _Cfg(), ledger


class TestInfraMarkers400:
    """SC2: 400/BadRequest/response_format 类真实文本命中 _is_infra_error。"""

    def test_real_400_texts_classified_infra(self):
        for err in (
            GATEWAY_400_RAW,
            REAL_CHECK_REASON_400,
            "LLM API 错误: deepseek-v4-pro — Error code: 400 - invalid_request_error",
            "openai.BadRequestError: Error code: 400",
            "This response_format type is unavailable now",
        ):
            assert _is_infra_error(err), err

    def test_each_marker_independently_covers_real_text(self):
        """三条新指纹各自都能单独兜住真机原文（任一被误删也有回归红）。"""
        for marker in ("Error code: 400", "BadRequestError", "response_format"):
            assert marker in GATEWAY_400_RAW, marker
            assert marker in runner._INFRA_ERROR_MARKERS, marker

    def test_content_rejections_still_not_infra(self):
        """质量判定的否决零触发——不能因为放宽指纹把内容拒绝也重掷。"""
        for err in (
            REAL_ADMISSION_REASON,
            "准入未通过: topic_relevance",
            "准入未通过: no_mechanism_signal",
            "修复不收敛: 5 项缺陷残留",
            "完整性地板未过（不可补偿）: concept F4：0 eligible 因果边",
            "compose_self_gate_failed: A2-frontmatter@frontmatter.related_concepts",
        ):
            assert not _is_infra_error(err), err


class TestAdmissionFailureProbe:
    """SC2: 底层 400 原文必须并入探测文本，否则永远命中不了。"""

    def test_probe_surfaces_underlying_400(self):
        checks = [
            AdmissionCheck("topic_relevance", False, REAL_CHECK_REASON_400),
            AdmissionCheck("how_tendency", False, REAL_CHECK_REASON_400),
        ]
        probe = _admission_failure_probe(REAL_ADMISSION_REASON, checks, "车道分类调用失败")
        assert "This response_format type is unavailable now" in probe
        assert "Error code: 400" in probe
        assert _is_infra_error(probe)

    def test_probe_of_real_content_rejection_stays_content(self):
        """真实内容拒绝（LLM 正常应答判 fail）不得被误判 infra。"""
        checks = [
            AdmissionCheck("topic_relevance", False, "内容讲融资动态，未命中任何领域锚点"),
            AdmissionCheck("how_tendency", True, "含可执行步骤"),
        ]
        probe = _admission_failure_probe("准入未通过: topic_relevance", checks)
        assert not _is_infra_error(probe)
        assert "含可执行步骤" not in probe  # 只并入失败项，不掺通过项噪声

    def test_probe_tolerates_empty_and_dict_checks(self):
        """mock 路径会传空列表或 dict —— 不得抛。"""
        assert _admission_failure_probe("准入未通过: how_tendency", []) == (
            "准入未通过: how_tendency"
        )
        probe = _admission_failure_probe(
            "准入未通过: novelty",
            [{"name": "novelty", "passed": False, "reason": REAL_CHECK_REASON_400}],
        )
        assert _is_infra_error(probe)


class TestAutoRequeueOn400:
    """SC2: 真实 abort 文本 → F39 自动重审恰好一次（预算 1 不变）。"""

    def test_budget_unchanged(self):
        assert AUTO_INFRA_REQUEUE_MAX == 1

    def test_400_abort_requeued_exactly_once(self, tmp_path: Path):
        cfg, ledger = _mk_config(tmp_path)
        probe = _admission_failure_probe(
            REAL_ADMISSION_REASON,
            [AdmissionCheck("topic_relevance", False, REAL_CHECK_REASON_400)],
        )
        assert _maybe_auto_infra_requeue(cfg, "h400", probe) is True
        assert _maybe_auto_infra_requeue(cfg, "h400", probe) is False
        events = [json.loads(x) for x in ledger.read_text().splitlines()]
        requeues = [e for e in events if e["event"] == "requeue"]
        assert len(requeues) == 1
        assert requeues[0]["details"]["reason"].startswith("auto_infra_retry(F39)")

    def test_400_requeue_reopens_article(self, tmp_path: Path):
        """闭环：abort 终态 + auto requeue → _is_processed=False（可重跑全链）。"""
        cfg, ledger = _mk_config(tmp_path)
        with open(ledger, "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "loop_id": "loop1",
                        "node": "admission",
                        "event": "abort",
                        "content_hash": "h400",
                        "ts": "2026-07-24T17:40:00Z",
                        "details": {"reason": REAL_ADMISSION_REASON},
                    }
                )
                + "\n"
            )
        assert runner._is_processed("h400", str(ledger)) is True
        _maybe_auto_infra_requeue(cfg, "h400", REAL_CHECK_REASON_400)
        assert runner._is_processed("h400", str(ledger)) is False

    def test_bare_admission_reason_alone_never_requeues(self, tmp_path: Path):
        """压实根因：只有整体 reason（无底层原文）时仍不重审——所以必须靠 probe。"""
        cfg, ledger = _mk_config(tmp_path)
        assert _maybe_auto_infra_requeue(cfg, "h1", REAL_ADMISSION_REASON) is False
        assert ledger.read_text() == ""


# ---------------------------------------------------------------------------
# 刀3 / SC3: abort 落账 failure_kind 二值归因
# ---------------------------------------------------------------------------


class TestFailureKindClassifier:
    def test_infra_and_content(self):
        assert _classify_failure_kind(REAL_CHECK_REASON_400) == FAILURE_KIND_INFRA
        assert _classify_failure_kind(REAL_ADMISSION_REASON) == FAILURE_KIND_CONTENT
        assert _classify_failure_kind("修复不收敛: 5 项缺陷残留") == FAILURE_KIND_CONTENT
        assert _classify_failure_kind("评分器双不可用") == FAILURE_KIND_INFRA


def _pipeline_config(tmp_path: Path):
    return runner.RunnerConfig(
        raw_dir=str(tmp_path / "raw"),
        ledger_path=str(tmp_path / "ledger.jsonl"),
        staging_dir=str(tmp_path / "staging"),
        vault_root=str(tmp_path / "vault"),
        failures_path=str(tmp_path / "failures.jsonl"),
        grader_result_dir=str(tmp_path / "grader"),
    )


def _stub_pre_admission(monkeypatch):
    monkeypatch.setattr(
        runner,
        "_load_vault_concept_index",
        lambda cfg, exclude_slug="": {
            "status": runner.LANE_INDEX_STATUS_FIRST_RUN,
            "wiki_references": [],
            "known_concepts": [],
            "pending_queue": [],
            "counts": {},
            "source_paths": [],
        },
    )
    monkeypatch.setattr(runner, "_existing_source_page_for_raw", lambda *a, **k: None)


def _admission_abort_event(ledger_path: str) -> dict:
    events = [
        json.loads(line)
        for line in Path(ledger_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    aborts = [e for e in events if e["event"] == "abort" and e["node"] == "admission"]
    assert aborts, "准入失败必须落 abort 事件"
    return aborts[-1]


class TestAdmissionAbortAttribution:
    """SC3: 准入 abort 事件带 failure_kind；infra / content 各自成立。"""

    def test_infra_abort_marked_and_requeued(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """上游 400 四判定全灭 → failure_kind=infra + 底层原文可见 + 自动重审一次。"""
        config = _pipeline_config(tmp_path)
        _stub_pre_admission(monkeypatch)
        checks = [
            AdmissionCheck(name, False, REAL_CHECK_REASON_400)
            for name in ("topic_relevance", "how_tendency", "novelty")
        ]
        monkeypatch.setattr(
            runner,
            "_run_admission_segment",
            lambda *a, **k: (
                False,
                REAL_ADMISSION_REASON,
                checks,
                "",
                "车道分类调用失败: LLM API 错误: deepseek-v4-pro — Error code: 400",
            ),
        )

        result = runner._run_pipeline_segments(
            "raw/concept--raw--2026-07-24.md", "RAW BODY", config
        )

        assert result["final_status"] == "rejected"
        event = _admission_abort_event(config.ledger_path)
        assert event["details"]["failure_kind"] == FAILURE_KIND_INFRA
        assert "This response_format type is unavailable now" in (
            event["details"]["underlying_error"]
        )
        # F39 自动重审恰好一次（同一篇不重复追加）
        events = [
            json.loads(x)
            for x in Path(config.ledger_path).read_text(encoding="utf-8").splitlines()
            if x.strip()
        ]
        requeues = [e for e in events if e["event"] == "requeue"]
        assert len(requeues) == 1, "上游故障死亡必须被 F39 重开，且只一次"

    def test_content_abort_marked_content_and_not_requeued(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """真实内容拒绝 → failure_kind=content，且不重审（质量判定的否决不重掷）。"""
        config = _pipeline_config(tmp_path)
        _stub_pre_admission(monkeypatch)
        checks = [
            AdmissionCheck("topic_relevance", False, "内容为融资动态，未命中领域锚点"),
        ]
        monkeypatch.setattr(
            runner,
            "_run_admission_segment",
            lambda *a, **k: (False, "准入未通过: topic_relevance", checks, "", ""),
        )

        result = runner._run_pipeline_segments(
            "raw/concept--raw--2026-07-24.md", "RAW BODY", config
        )

        assert result["final_status"] == "rejected"
        event = _admission_abort_event(config.ledger_path)
        assert event["details"]["failure_kind"] == FAILURE_KIND_CONTENT
        events = [
            json.loads(x)
            for x in Path(config.ledger_path).read_text(encoding="utf-8").splitlines()
            if x.strip()
        ]
        assert not [e for e in events if e["event"] == "requeue"]

    def test_duplicate_source_abort_marked_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """去重拒收是内容判定——failure_kind 不能时有时无。"""
        config = _pipeline_config(tmp_path)
        monkeypatch.setattr(
            runner,
            "_load_vault_concept_index",
            lambda cfg, exclude_slug="": {
                "status": runner.LANE_INDEX_STATUS_FIRST_RUN,
                "wiki_references": [],
                "known_concepts": [],
                "pending_queue": [],
                "counts": {},
                "source_paths": [],
            },
        )
        monkeypatch.setattr(
            runner,
            "_existing_source_page_for_raw",
            lambda *a, **k: Path(tmp_path / "vault" / "wiki" / "sources" / "x.md"),
        )
        runner._run_pipeline_segments(
            "raw/concept--raw--2026-07-24.md", "RAW BODY", config
        )
        event = _admission_abort_event(config.ledger_path)
        assert event["details"]["failure_kind"] == FAILURE_KIND_CONTENT


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
