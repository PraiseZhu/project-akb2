"""test_llm_router.py — 路由模块测试（全 fixture，不打真 API）。

覆盖:
    - 缺 key 显式抛 RouterConfigError
    - -preview 模型拒绝
    - 多模型路由
    - 预算具名常量
    - reasoning_effort 透传验证
    - per-article 调用预算强制（sc-p3-max-calls-enforced）：第 N+1 次同篇调用
      fail-closed 并落 quality 账本
"""

from __future__ import annotations

import json
import os
import plistlib
import stat
import subprocess
import sys
import types
from unittest import mock

import pytest

from src.akb2.quality.llm_router import (
    ARTICLE_BUDGET_EVENT,
    MAX_CALLS_PER_ARTICLE,
    MAX_RETRIES,
    TIMEOUT_S,
    ArticleBudgetExceededError,
    RouterConfigError,
    build_deepseek_beta_tool_payload,
    build_outbound_payload,
    route_llm,
)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _install_fake_openai(monkeypatch) -> dict:
    """注入假 openai 模块，捕获 create() 是否被调用（不打真 API）。"""
    captured: dict[str, object] = {"create_calls": 0}

    class _FakeCompletions:
        def create(self, **kwargs):
            captured["create_calls"] = int(captured["create_calls"]) + 1
            message = types.SimpleNamespace(content='{"ok": true}')
            choice = types.SimpleNamespace(message=message)
            return types.SimpleNamespace(choices=[choice])

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=_FakeCompletions())

    fake_openai = types.SimpleNamespace(
        OpenAI=_FakeOpenAI,
        APITimeoutError=TimeoutError,
        APIError=RuntimeError,
    )
    monkeypatch.setitem(__import__("sys").modules, "openai", fake_openai)
    return captured


class TestRouterConfig:
    """路由配置验证。"""

    def test_rejects_preview_model(self):
        """模型名含 -preview 直接拒绝。"""
        with pytest.raises(RouterConfigError, match="预览模型"):
            route_llm("deepseek-v4-pro-preview", [{"role": "user", "content": "hi"}])

    def test_rejects_unknown_model(self):
        """不支持的模型名抛错。"""
        with pytest.raises(RouterConfigError, match="不支持的模型家族"):
            route_llm("unknown-model", [{"role": "user", "content": "hi"}])

    def test_rejects_empty_model(self):
        """空模型名抛错。"""
        with pytest.raises(RouterConfigError):
            route_llm("", [{"role": "user", "content": "hi"}])

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_missing_key_raises(self):
        """环境变量缺失时抛 RouterConfigError。"""
        with pytest.raises(RouterConfigError, match="API key 缺失"):
            route_llm("gemini-flash", [{"role": "user", "content": "hi"}])

    @mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=True)
    def test_empty_key_raises(self):
        """环境变量为空字符串时抛 RouterConfigError。"""
        with pytest.raises(RouterConfigError, match="API key 缺失"):
            route_llm("gemini-flash", [{"role": "user", "content": "hi"}])

    @mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test-key"}, clear=True)
    def test_deepseek_key_present_no_error_on_config(self):
        """DeepSeek key 存在时配置验证通过（不实际发请求则不会触发 API 错误）。"""
        # 不实际发请求，只验证 key 解析不抛错
        from src.akb2.quality.llm_router import _get_api_key, _validate_model_family

        config = _validate_model_family("deepseek-v4-pro")
        assert config["model_id"] == "deepseek-v4-pro"
        assert config["base_url"] == "https://api.deepseek.com/v1"

        key = _get_api_key("DEEPSEEK_API_KEY")
        assert key == "sk-test-key"

    @mock.patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "gw-test-key"}, clear=True)
    def test_gpt54_uses_gateway_key_when_openai_key_missing(self):
        """本机只配置统一网关 key 时，gpt-5.4 不应误走官方 OpenAI/误报 OPENAI_API_KEY 缺失。"""
        from src.akb2.quality.llm_router import _get_api_key, _validate_model_family

        config = _validate_model_family("gpt-5.4")

        assert config["base_url"] == "https://llm-proxy.tapsvc.com/v1"
        assert config["model_id"] == "gpt-5.4"
        assert "AI_GATEWAY_API_KEY" in config["key_env_candidates"]
        assert _get_api_key(config["key_env_candidates"]) == "gw-test-key"

    @mock.patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "gw-test-key"}, clear=True)
    def test_deepseek_uses_gateway_model_id_when_gateway_selected(self):
        """统一网关下 DeepSeek 渠道名是 provider/model，不能误用 family id。"""
        from src.akb2.quality.llm_router import _get_api_key, _validate_model_family

        config = _validate_model_family("deepseek-v4-pro")

        assert config["base_url"] == "https://llm-proxy.tapsvc.com/v1"
        assert config["model_id"] == "deepseek/deepseek-v4-pro"
        assert _get_api_key(config["key_env_candidates"]) == "gw-test-key"

    @mock.patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "gw-test-key"}, clear=True)
    def test_gemini_uses_supported_gateway_model_id_when_gateway_selected(self):
        """统一网关下 Gemini 默认使用当前网关支持的非 preview Flash 模型。"""
        from src.akb2.quality.llm_router import _get_api_key, _validate_model_family

        config = _validate_model_family("gemini-flash")

        assert config["base_url"] == "https://llm-proxy.tapsvc.com/v1"
        assert config["model_id"] == "gemini-3.5-flash"
        assert "-preview" not in config["model_id"]
        assert _get_api_key(config["key_env_candidates"]) == "gw-test-key"

    @mock.patch.dict(
        os.environ,
        {
            "OPENROUTER_API_KEY": "or-test-key",
            "OPENROUTER_BASE_URL": "https://llm-proxy.tapsvc.com",
        },
        clear=True,
    )
    def test_openrouter_gateway_aliases_are_normalised(self):
        """历史 OPENROUTER_* 变量可作为统一网关配置，裸 base 自动补 /v1。"""
        from src.akb2.quality.llm_router import _get_api_key, _validate_model_family

        config = _validate_model_family("gpt-5.4")

        assert config["base_url"] == "https://llm-proxy.tapsvc.com/v1"
        assert "OPENROUTER_API_KEY" in config["key_env_candidates"]
        assert _get_api_key(config["key_env_candidates"]) == "or-test-key"

    @mock.patch.dict(
        os.environ,
        {
            "OPENAI_BASE_URL": "https://llm-proxy.tapsvc.com/v1",
            "AI_GATEWAY_API_KEY": "gw-test-key",
        },
        clear=True,
    )
    def test_explicit_openai_gateway_base_can_use_gateway_key(self):
        """显式 OPENAI_BASE_URL 指向网关时，也允许复用 AI_GATEWAY_API_KEY。"""
        from src.akb2.quality.llm_router import _get_api_key, _validate_model_family

        config = _validate_model_family("gpt-5.4")

        assert config["base_url"] == "https://llm-proxy.tapsvc.com/v1"
        assert _get_api_key(config["key_env_candidates"]) == "gw-test-key"


class TestBudgetConstants:
    """预算具名常量验证。"""

    def test_max_calls_per_article_is_named_constant(self):
        """MAX_CALLS_PER_ARTICLE 是具名常量（宪法 P4）。"""
        assert isinstance(MAX_CALLS_PER_ARTICLE, int)
        assert MAX_CALLS_PER_ARTICLE > 0
        assert MAX_CALLS_PER_ARTICLE == 5

    def test_timeout_s_is_named_constant(self):
        """TIMEOUT_S 是具名常量（宪法 P4）。"""
        assert isinstance(TIMEOUT_S, (int, float))
        assert TIMEOUT_S > 0
        assert TIMEOUT_S == 120.0

    def test_max_retries_is_named_constant(self):
        """MAX_RETRIES 是显式具名常量，SDK 默认重试不可隐式漂移。"""
        assert isinstance(MAX_RETRIES, int)
        assert MAX_RETRIES == 2


class TestArticleBudgetEnforcement:
    """sc-p3-max-calls-enforced：per-article 调用预算真正强制（不只是常量相等）。

    计数真相源是 AKB2_STATE_ROOT 下的账本目录（tests/quality/conftest.py 的
    autouse fixture 已隔离到 tmp），不打真 API——openai 模块整体被替换成假实现。

    ⚠ 环境基线用 fixture 内 monkeypatch.setenv 合并而非 mock.patch.dict(clear=True)：
    clear=True 会把 autouse fixture 钉好的 AKB2_STATE_ROOT 一并清掉，测试就写进
    真实生产状态目录（本测试文件曾实测污染 ~/Library/Application Support/akb2）。
    """

    def _env(self, monkeypatch) -> None:
        """清网关相关变量但保留 AKB2_STATE_ROOT 等隔离变量，再注入假 key。"""
        for name in (
            "AI_GATEWAY_API_KEY",
            "OPENROUTER_API_KEY",
            "TAPTAP_API_KEY",
            "OPENAI_BASE_URL",
            "DEEPSEEK_BASE_URL",
            "GEMINI_BASE_URL",
            "AI_GATEWAY_BASE_URL",
            "OPENROUTER_BASE_URL",
            "TAPTAP_BASE_URL",
        ):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("AI_GATEWAY_API_KEY", "gw-test-key")

    def _mk_msg(self, i: int = 0):
        return [{"role": "user", "content": f"call-{i}"}]

    def test_nth_plus_one_call_same_article_fail_closed(self, monkeypatch, tmp_path):
        """同一 article_key 第 MAX_CALLS_PER_ARTICLE+1 次 route_llm 被拒绝。

        前 N 次正常返回（假 openai），第 N+1 次必须抛 ArticleBudgetExceededError
        且 **不发起 API 请求**（超限判定在请求之前）。
        """
        self._env(monkeypatch)
        captured = _install_fake_openai(monkeypatch)

        key = "article-alpha"
        for i in range(MAX_CALLS_PER_ARTICLE):
            resp = route_llm(
                "gpt-5.4", self._mk_msg(i), article_key=key
            )
            assert json.loads(resp.content) == {"ok": True}
        assert int(captured["create_calls"]) == MAX_CALLS_PER_ARTICLE

        with pytest.raises(ArticleBudgetExceededError, match="MAX_CALLS_PER_ARTICLE"):
            route_llm("gpt-5.4", self._mk_msg(99), article_key=key)

        # 超限的那次调用没打到 API：请求计数停在 N
        assert int(captured["create_calls"]) == MAX_CALLS_PER_ARTICLE

    def test_exceeded_event_lands_in_quality_ledger(self, monkeypatch, tmp_path):
        """第 N+1 次超限拒绝把 llm_article_budget_exceeded 事件落进 quality 账本。"""
        self._env(monkeypatch)
        _install_fake_openai(monkeypatch)
        from src.akb2.common.state_paths import (
            LEDGER_SUBDIR,
            resolve_state_file,
        )

        key = "article-ledger"
        for i in range(MAX_CALLS_PER_ARTICLE):
            route_llm("gpt-5.4", self._mk_msg(i), article_key=key)
        with pytest.raises(ArticleBudgetExceededError):
            route_llm("gpt-5.4", self._mk_msg(99), article_key=key)

        ledger = resolve_state_file("quality-ledger.jsonl", subdir=LEDGER_SUBDIR)
        assert ledger.exists(), "超限事件必须落账（宪法 P4 失败可见）"
        rows = [
            json.loads(line)
            for line in ledger.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        events = [r for r in rows if r.get("event") == ARTICLE_BUDGET_EVENT]
        assert len(events) == 1
        row = events[0]
        # loop-ledger.schema.json 必填字段齐全
        for field in ("loop_id", "node", "event", "content_hash", "ts"):
            assert field in row and row[field] != ""
        assert row["event"] == ARTICLE_BUDGET_EVENT
        assert row["budget"]["calls_limit"] == MAX_CALLS_PER_ARTICLE
        assert row["details"]["model_family"] == "gpt-5.4"

    def test_distinct_articles_do_not_share_budget(self, monkeypatch):
        """不同 article_key 各自有独立预算——计数按 article，不是全局。"""
        self._env(monkeypatch)
        captured = _install_fake_openai(monkeypatch)

        for i in range(MAX_CALLS_PER_ARTICLE):
            route_llm("gpt-5.4", self._mk_msg(i), article_key="article-a")
        # article-b 不受 article-a 打满影响
        resp = route_llm("gpt-5.4", self._mk_msg(0), article_key="article-b")
        assert json.loads(resp.content) == {"ok": True}
        assert int(captured["create_calls"]) == MAX_CALLS_PER_ARTICLE + 1

    def test_no_article_key_keeps_legacy_behaviour(self, monkeypatch):
        """article_key=None（既有调用方）不参与计数，行为不变。"""
        self._env(monkeypatch)
        captured = _install_fake_openai(monkeypatch)

        for i in range(MAX_CALLS_PER_ARTICLE + 3):
            resp = route_llm("gpt-5.4", self._mk_msg(i))
            assert json.loads(resp.content) == {"ok": True}
        assert int(captured["create_calls"]) == MAX_CALLS_PER_ARTICLE + 3

    def test_direct_and_subprocess_entries_share_one_counter(self, monkeypatch, tmp_path):
        """函数入口与子进程（repair_worker 形态）入口同一计数（boundary 要求）。

        用真实 subprocess 起新 Python 进程直调 route_llm——若计数只在本进程内存，
        子进程计数会归零绕过预算；计数真相源在账本文件时，子进程打满后父进程
        第 N+1 次必须被拒。
        """
        self._env(monkeypatch)
        _install_fake_openai(monkeypatch)

        # 子进程跑在真实环境（无假 openai 注入），只跑 N 次、每次给假 key 但
        # openai 导入会失败——所以子进程里改用「预期内抛 RouterConfigError 前
        # 先消耗名额」不行：名额预留发生在 _get_api_key 之后。改用直接调用
        # reserve_article_call 验证跨进程同源。
        root_literal = json.dumps(PROJECT_ROOT)
        script = (
            f"import sys; sys.path.insert(0, {root_literal});"
            "from src.akb2.quality.llm_router import reserve_article_call;"
            f"results = [reserve_article_call('article-x') for _ in range({MAX_CALLS_PER_ARTICLE})];"
            "print(results)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == f"{[True] * MAX_CALLS_PER_ARTICLE}"

        # 子进程已把 article-x 名额打满 → 本进程（route_llm 直调入口）第 N+1 次被拒
        captured = _install_fake_openai(monkeypatch)
        with pytest.raises(ArticleBudgetExceededError, match="MAX_CALLS_PER_ARTICLE"):
            route_llm("gpt-5.4", self._mk_msg(0), article_key="article-x")
        assert int(captured["create_calls"]) == 0

    def test_corrupt_counter_file_fails_closed(self, monkeypatch, tmp_path):
        """计数文件损坏时拒绝放行（无法证明还有名额 → fail-closed）。"""
        self._env(monkeypatch)
        _install_fake_openai(monkeypatch)
        from src.akb2.common.state_paths import LEDGER_SUBDIR, get_state_root
        from src.akb2.quality.llm_router import ARTICLE_CALLS_FILE

        counter_path = get_state_root() / LEDGER_SUBDIR / ARTICLE_CALLS_FILE
        counter_path.parent.mkdir(parents=True, exist_ok=True)
        counter_path.write_text("{not-valid-json", encoding="utf-8")

        with pytest.raises(ArticleBudgetExceededError, match="计数文件损坏"):
            route_llm("gpt-5.4", self._mk_msg(0), article_key="article-corrupt")


class TestBuildOutboundPayload:
    """出站 payload 构建验证（fixture 断言，不打真 API）。"""

    def test_deepseek_payload_has_reasoning_effort(self):
        """DeepSeek 请求体必须显式带 reasoning_effort: max。"""
        payload = build_outbound_payload(
            "deepseek-v4-pro",
            [{"role": "user", "content": "test"}],
        )
        assert "reasoning_effort" in payload
        assert payload["reasoning_effort"] == "max"

    def test_deepseek_payload_can_disable_reasoning_effort(self):
        """distill 可显式关闭 reasoning_effort，避免 Think Max 卡死。"""
        payload = build_outbound_payload(
            "deepseek-v4-pro",
            [{"role": "user", "content": "test"}],
            reasoning_effort=None,
        )
        assert "reasoning_effort" not in payload

    def test_gpt_payload_no_reasoning_effort(self):
        """GPT-5.4 请求体不应含 reasoning_effort。"""
        payload = build_outbound_payload(
            "gpt-5.4",
            [{"role": "user", "content": "test"}],
        )
        assert "reasoning_effort" not in payload

    def test_gemini_payload_no_reasoning_effort(self):
        """Gemini Flash 请求体不应含 reasoning_effort。"""
        payload = build_outbound_payload(
            "gemini-flash",
            [{"role": "user", "content": "test"}],
        )
        assert "reasoning_effort" not in payload

    def test_payload_has_response_format_when_schema_provided(self):
        """提供 response_schema 时 payload 含 response_format。"""
        schema = {"type": "object", "properties": {"pass": {"type": "boolean"}}}
        payload = build_outbound_payload(
            "gpt-5.4",
            [{"role": "user", "content": "test"}],
            response_schema=schema,
        )
        assert "response_format" in payload
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["strict"] is True

    def test_payload_can_send_non_strict_schema(self):
        """少数兼容模型可显式 strict_schema=False，但默认仍 strict=True。"""
        schema = {"type": "object", "properties": {"pass": {"type": "boolean"}}}
        payload = build_outbound_payload(
            "gpt-5.4",
            [{"role": "user", "content": "test"}],
            response_schema=schema,
            strict_schema=False,
        )
        assert payload["response_format"]["json_schema"]["strict"] is False

    def test_payload_no_response_format_without_schema(self):
        """不提供 response_schema 时 payload 不含 response_format。"""
        payload = build_outbound_payload(
            "gpt-5.4",
            [{"role": "user", "content": "test"}],
        )
        assert "response_format" not in payload

    def test_all_three_models_have_messages(self):
        """三种模型家族的 payload 都含 messages。"""
        for family in ["deepseek-v4-pro", "gpt-5.4", "gemini-flash"]:
            payload = build_outbound_payload(
                family,
                [{"role": "user", "content": "test"}],
            )
            assert "messages" in payload
            assert payload["messages"] == [{"role": "user", "content": "test"}]
            assert "model" in payload

    def test_deepseek_beta_tool_payload_simplifies_schema(self):
        """DeepSeek beta strict tool schema 去掉 unsupported 校验词并 required 覆盖 properties。"""
        schema = {
            "type": "object",
            "properties": {
                "claims": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "minLength": 3},
                            "anchor": {"type": "string"},
                        },
                        "required": ["text"],
                        "additionalProperties": True,
                    },
                    "minItems": 1,
                }
            },
            "required": ["claims"],
            "additionalProperties": False,
        }
        payload = build_deepseek_beta_tool_payload(
            [{"role": "user", "content": "test"}],
            schema,
            model_id="deepseek-chat",
        )

        function = payload["tools"][0]["function"]
        parameters = function["parameters"]
        claim_item = parameters["properties"]["claims"]["items"]

        assert payload["model"] == "deepseek-chat"
        assert payload["tool_choice"]["function"]["name"] == "akb2_extract_segment"
        assert function["strict"] is True
        assert "minItems" not in parameters["properties"]["claims"]
        assert "minLength" not in claim_item["properties"]["text"]
        assert claim_item["required"] == ["text", "anchor"]
        assert claim_item["additionalProperties"] is False


class TestRouteLLMRuntimeOptions:
    """route_llm 新 runtime 参数必须真正落到 OpenAI SDK 构造/请求。"""

    @mock.patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "gw-test-key"}, clear=True)
    def test_runtime_options_are_passed_to_openai_client(self, monkeypatch):
        """DeepSeek distill gate: timeout=60/max_retries=0/reasoning_effort=None。"""
        captured_client: dict[str, object] = {}
        captured_create: dict[str, object] = {}

        class _FakeCompletions:
            def create(self, **kwargs):
                captured_create.update(kwargs)
                message = types.SimpleNamespace(content='{"ok": true}')
                choice = types.SimpleNamespace(message=message)
                return types.SimpleNamespace(choices=[choice])

        class _FakeOpenAI:
            def __init__(self, **kwargs):
                captured_client.update(kwargs)
                self.chat = types.SimpleNamespace(
                    completions=_FakeCompletions()
                )

        fake_openai = types.SimpleNamespace(
            OpenAI=_FakeOpenAI,
            APITimeoutError=TimeoutError,
            APIError=RuntimeError,
        )
        monkeypatch.setitem(__import__("sys").modules, "openai", fake_openai)

        result = route_llm(
            "deepseek-v4-pro",
            [{"role": "user", "content": "test"}],
            response_schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
            timeout_s=60,
            max_retries=0,
            reasoning_effort=None,
            max_tokens=3000,
            extra_body={"enable_thinking": False},
        )

        assert result.content == {"ok": True}
        assert captured_client["timeout"] == 60
        assert captured_client["max_retries"] == 0
        assert "reasoning_effort" not in captured_create
        assert captured_create["max_tokens"] == 3000
        assert captured_create["extra_body"] == {"enable_thinking": False}


class TestMainPipelineEnv:
    """main-pipeline 启动包装层的 env 归一化验证。"""

    def test_main_pipeline_sources_env_and_exports_gateway_aliases(self, tmp_path):
        home = tmp_path / "home"
        env_dir = home / ".config" / "akb2"
        env_dir.mkdir(parents=True)
        (env_dir / "github-briefing.env").write_text(
            "\n".join(
                [
                    "OPENROUTER_API_KEY=or-test-key",
                    "OPENROUTER_BASE_URL=https://llm-proxy.tapsvc.com",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        fake_python = tmp_path / "python3"
        fake_python.write_text(
            """#!/usr/bin/env bash
if [ "$1" = "-c" ]; then
  echo "3.11"
  exit 0
fi
printf 'AI_GATEWAY_API_KEY=%s\\n' "${AI_GATEWAY_API_KEY:-}"
printf 'OPENAI_BASE_URL=%s\\n' "${OPENAI_BASE_URL:-}"
printf 'OPENAI_MODEL_ID=%s\\n' "${OPENAI_MODEL_ID:-}"
printf 'DEEPSEEK_MODEL_ID=%s\\n' "${DEEPSEEK_MODEL_ID:-}"
exit 0
""",
            encoding="utf-8",
        )
        fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)

        env = {
            "HOME": str(home),
            "AKB2_PYTHON_BIN": str(fake_python),
            "AKB2_LOG_DIR": str(tmp_path / "logs"),
            "AKB2_RUNNER_LOG": str(tmp_path / "logs" / "runner.log"),
            "AKB2_LOCK_FILE": str(tmp_path / "locks" / "runner.lock"),
            "PATH": os.environ.get("PATH", ""),
        }
        result = subprocess.run(
            ["/bin/bash", os.path.join(PROJECT_ROOT, "scripts", "akb2-main-pipeline.sh"), "--once"],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        log_path = tmp_path / "logs" / "runner.log"
        log_tail = open(log_path, encoding="utf-8").read().splitlines()[-10:]
        combined = "\n".join([result.stdout, result.stderr, *log_tail])

        assert result.returncode == 0
        assert "AI_GATEWAY_API_KEY=or-test-key" in combined
        assert "OPENAI_BASE_URL=https://llm-proxy.tapsvc.com/v1" in combined
        assert "OPENAI_MODEL_ID=gpt-5.4" in combined
        assert "DEEPSEEK_MODEL_ID=deepseek/deepseek-v4-pro" in combined

    def test_main_pipeline_does_not_double_append_v1(self, tmp_path):
        home = tmp_path / "home"
        env_dir = home / ".config" / "akb2"
        env_dir.mkdir(parents=True)
        (env_dir / "github-briefing.env").write_text(
            "\n".join(
                [
                    "AI_GATEWAY_API_KEY=gw-test-key",
                    "AI_GATEWAY_BASE_URL=https://llm-proxy.tapsvc.com/v1",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        fake_python = tmp_path / "python3"
        fake_python.write_text(
            """#!/usr/bin/env bash
if [ "$1" = "-c" ]; then
  echo "3.11"
  exit 0
fi
printf 'OPENAI_BASE_URL=%s\\n' "${OPENAI_BASE_URL:-}"
exit 0
""",
            encoding="utf-8",
        )
        fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)

        env = {
            "HOME": str(home),
            "AKB2_PYTHON_BIN": str(fake_python),
            "AKB2_LOG_DIR": str(tmp_path / "logs"),
            "AKB2_RUNNER_LOG": str(tmp_path / "logs" / "runner.log"),
            "AKB2_LOCK_FILE": str(tmp_path / "locks" / "runner.lock"),
            "PATH": os.environ.get("PATH", ""),
        }
        result = subprocess.run(
            ["/bin/bash", os.path.join(PROJECT_ROOT, "scripts", "akb2-main-pipeline.sh"), "--once"],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        log_tail = (tmp_path / "logs" / "runner.log").read_text(encoding="utf-8").splitlines()[-10:]
        combined = "\n".join([result.stdout, result.stderr, *log_tail])

        assert result.returncode == 0
        assert "OPENAI_BASE_URL=https://llm-proxy.tapsvc.com/v1" in combined
        assert "https://llm-proxy.tapsvc.com/v1/v1" not in combined

    def test_main_pipeline_plist_uses_existing_vault_root(self):
        """launchd main-pipeline 不能指向当前机器不存在的 AKB2 vault。"""
        plist_path = os.path.join(PROJECT_ROOT, "launchd", "com.akb2.main-pipeline.plist")
        with open(plist_path, "rb") as f:
            plist = plistlib.load(f)

        env = plist["EnvironmentVariables"]
        vault_root = env["AKB2_VAULT_ROOT"]

        assert vault_root.endswith("Praise Space/AKB2.0")
        # 仓库内 plist 有两种确定形态，都显式验收（不 skip——skip 会把部署回归
        # 藏进 "绿" 里）：
        #   1. 未安装模板：占位符 __AKB2_VAULT_ROOT__（bootstrap 脚本负责替换）
        #   2. 已安装实例：真实路径，且必须真实挂载存在
        if "__AKB2_VAULT_ROOT__" in vault_root:
            # 模板态：占位符必须原样保留，禁止提交时预替换成某台机器的路径
            assert vault_root.startswith("__AKB2_VAULT_ROOT__")
        else:
            assert os.path.exists(vault_root), (
                f"plist 指向已实例化的 vault 但该路径不存在: {vault_root}"
                "（launchd 安装属部署环境；红线不动 launchd/plist）"
            )
