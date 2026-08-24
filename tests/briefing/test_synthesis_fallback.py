"""social briefing 合成链的失败语义与模型合规测试。

修复的两件事：

**L1-6 明文违宪** — `_SYNTHESIS_MODEL` 默认值曾是 `gemini-3.1-pro-preview`，而
`AKB2_SYNTHESIS_MODEL` 全仓仅出现在那一行，两个生产 env 文件（`~/.akb2-secrets.sh`、
`~/.config/akb2/github-briefing.env`）与 launchd plist 都没有覆盖它 → 生产 7×24
一直跑在预览版模型上。`docs/constitution.md:95` 明令禁止，rationale 正是
「预览版限流不受控」，而 `docs/allowed-residuals.md` grep preview 零命中 = 未豁免。

**L1-7 两条链语义相反** — social 链 `except Exception` → WARNING →
`return _fallback_synthesis(...)`，把一份 summary 明文写着
"This is a fallback summary generated when the synthesis model is unavailable"、
topic 恒为 "AI Technology Trends" 的**降级假简报**写进 vault；同仓 github 链
遇到同样情形是 vault 零写入 + exit 1。两种语义并存且都未登记豁免。
现统一为 github 侧的 fail-closed。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from akb2.briefing import synthesis as syn

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_PATH = REPO_ROOT / "tests/fixtures/briefing/scored-sample.json"

# 旧降级假简报的指纹 —— 一旦这段文字能出现在 synthesize() 的返回值里，
# 说明「API 失败 → 写假简报」这条自动路径又回来了。
_FAKE_BRIEFING_FINGERPRINT = "fallback summary generated when the synthesis model"


def _load_scored() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


def _valid_model_output(scored: dict) -> str:
    top = sorted(scored["tweets"], key=lambda t: t.get("score", 0), reverse=True)
    ids = [str(t["id"]) for t in top[:3]]
    return json.dumps({
        "executive_summary": "今日要点综述，涵盖具体工具与指标。",
        "focus_discussions": [{
            "topic": "智能体长记忆基础设施",
            "summary": "第一句。第二句。第三句。",
            "supporting_tweet_ids": ids,
        }],
    })


def _raising_post(exc: Exception):
    def _post(*args, **kwargs):
        raise exc
    return _post


def _status_error(code: int, body: str = "") -> Exception:
    """构造 httpx 的 HTTPStatusError（`_call_llm` 是靠 raise_for_status 抛它）。"""
    import httpx

    request = httpx.Request("POST", "https://llm-proxy.tapsvc.com/v1/chat/completions")
    response = httpx.Response(code, request=request, text=body)
    return httpx.HTTPStatusError(f"HTTP {code}", request=request, response=response)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    """每个用例都从干净的模型/降级 env + 干净的族可用性闩开始，避免相互污染。"""
    monkeypatch.delenv("AKB2_SYNTHESIS_MODEL", raising=False)
    monkeypatch.delenv(syn._SYNTHESIS_REASONING_EFFORT_ENV, raising=False)
    monkeypatch.delenv(syn._FAMILY_FALLBACK_ENV, raising=False)
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "test-key")
    syn._unavailable_families.clear()
    yield
    syn._unavailable_families.clear()


# ═══════════════════════════════════════════════════════════════════════════
# SC-8 — `-preview` 违宪修正（宪法 P7 / docs/constitution.md:95）
# ═══════════════════════════════════════════════════════════════════════════

class TestPreviewModelForbidden:
    def test_default_model_is_not_preview(self):
        """SC-8：默认值必须是正式版。修复前是 gemini-3.1-pro-preview。"""
        assert "-preview" not in syn._DEFAULT_SYNTHESIS_MODEL, syn._DEFAULT_SYNTHESIS_MODEL
        assert "-preview" not in syn._SYNTHESIS_MODEL, syn._SYNTHESIS_MODEL
        assert "-preview" not in syn._FALLBACK_SYNTHESIS_MODEL

    def test_default_matches_sibling_stage_convention(self):
        """与同管线 translate.py 的主力模型同族（都是 deepseek/），合成走 flash。"""
        from akb2.briefing.translate import _TRANSLATE_MODEL

        assert syn._DEFAULT_SYNTHESIS_MODEL.startswith("deepseek/")
        assert _TRANSLATE_MODEL.startswith("deepseek/")
        assert syn._DEFAULT_SYNTHESIS_MODEL == "deepseek/deepseek-v4-flash"
        assert syn._DEFAULT_SYNTHESIS_REASONING_EFFORT == "max"

    def test_explicit_preview_env_raises_on_call(self, monkeypatch):
        """SC-8：显式设 preview 模型 → 调用即抛，不靠 env 自觉。"""
        monkeypatch.setenv("AKB2_SYNTHESIS_MODEL", "gemini-3.1-pro-preview")
        posts: list[int] = []
        monkeypatch.setattr(
            "httpx.post", lambda *a, **k: posts.append(1),
        )

        with pytest.raises(syn.SynthesisUnavailableError) as exc:
            syn._call_deepseek("prompt")

        msg = str(exc.value)
        assert "preview model is forbidden for 7x24 pipeline" in msg, msg
        assert "gemini-3.1-pro-preview" in msg
        assert posts == [], "预览版误配还真去打了一次请求"

    def test_preview_misconfig_is_not_masked_by_family_fallback(self, monkeypatch):
        """预览版误配是部署错误 —— 换族会把违宪配置掩盖成「主族偶发失败」。"""
        monkeypatch.setenv("AKB2_SYNTHESIS_MODEL", "x-preview")
        seen_models: list[str] = []

        def fake_post(*args, **kwargs):
            seen_models.append(kwargs["json"]["model"])
            raise AssertionError("不该发出任何请求")

        monkeypatch.setattr("httpx.post", fake_post)

        with pytest.raises(syn.SynthesisUnavailableError, match="forbidden"):
            syn.synthesize(_load_scored(), date="2026-08-02")
        assert seen_models == []

    def test_preview_check_covers_arbitrary_vendor_prefix(self):
        """校验看的是 `-preview` 子串，不是某个厂商前缀白名单。"""
        with pytest.raises(syn.SynthesisUnavailableError):
            syn._reject_preview_model("some-vendor/model-preview", "unit")
        assert syn._reject_preview_model("deepseek/deepseek-v4-flash", "unit") == (
            "deepseek/deepseek-v4-flash"
        )


# ═══════════════════════════════════════════════════════════════════════════
# SC-9 — fail-closed：模型族降级用尽后不写降级假简报
# ═══════════════════════════════════════════════════════════════════════════

class TestFailClosedOnApiFailure:
    def test_429_on_both_families_raises_instead_of_fake_briefing(self, monkeypatch):
        """SC-9：有 key 且 API 抛 429 → 合成入口 raise，不返回含 fallback 文案的 payload。"""
        monkeypatch.setattr("httpx.post", _raising_post(_status_error(429, "rate limited")))
        monkeypatch.setattr("time.sleep", lambda s: None)

        with pytest.raises(syn.SynthesisUnavailableError) as exc:
            syn.synthesize(_load_scored(), date="2026-08-02")

        msg = str(exc.value)
        assert "两个模型族均失败" in msg or "fail-closed" in msg
        assert _FAKE_BRIEFING_FINGERPRINT not in msg

    def test_no_fake_payload_can_be_returned_on_api_failure(self, monkeypatch):
        """守护断言：任何 API 失败路径都不得**返回**（而非抛出）一份 payload。

        旧行为最危险的地方不是失败，而是失败后返回一份能过 schema 的合规 payload ——
        下游 audit / vault 写入完全看不出今天没有真实合成。
        """
        monkeypatch.setattr("httpx.post", _raising_post(_status_error(500, "boom")))
        monkeypatch.setattr("time.sleep", lambda s: None)

        try:
            result = syn.synthesize(_load_scored(), date="2026-08-02")
        except syn.SynthesisUnavailableError:
            return  # 正确行为
        pytest.fail(
            f"API 全失败却返回了 payload（source={result.get('source')}）—— "
            "降级假简报路径仍在"
        )

    def test_quality_degradation_still_retries_before_giving_up(self, monkeypatch):
        """回归守护：合成退化（非调用失败）仍走原有 3 次重试，不被 fail-closed 抢跑。"""
        scored = _load_scored()
        top = sorted(scored["tweets"], key=lambda t: t.get("score", 0), reverse=True)
        ids = [str(t["id"]) for t in top[:3]]
        degraded = json.dumps(
            {"focus_discussions": [{"topic": "退化主题", "supporting_tweet_ids": ids}]}
        )
        responses = [degraded, _valid_model_output(scored)]

        def fake_call(prompt, max_retries=2):
            return responses.pop(0)

        with patch.object(syn, "_call_deepseek", side_effect=fake_call):
            result = syn.synthesize(scored, date="2026-08-02")

        assert result["focus_discussions"][0]["topic"] == "智能体长记忆基础设施"
        assert responses == []

    def test_missing_key_path_is_unchanged(self, monkeypatch):
        """本轮只摘「API 失败 → 假简报」这条路径；无 key 的确定性 fallback 保持原样。

        无 key 时仍自动 fallback 是**已知残留**，已登记 docs/allowed-residuals.md
        （`synthesis_no_key_fallback`）待 owner 复核 —— 这里锁住当前行为，
        以便日后真要改时这条会响。
        """
        monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
        result = syn.synthesize(_load_scored(), date="2026-08-02")
        assert result["source"]["name"] == "synthesis_fallback"
        assert _FAKE_BRIEFING_FINGERPRINT in result["focus_discussions"][0]["summary"]


# ═══════════════════════════════════════════════════════════════════════════
# 模型族降级 —— 「降级用尽后才 fail-closed」，不是「一挂就拒」
# ═══════════════════════════════════════════════════════════════════════════

class TestSocialFamilyFallback:
    def test_primary_failure_degrades_to_fallback_family_visibly(self, monkeypatch, capsys):
        """主族挂 → 换备族并产出简报；降级必须打 stdout（宪法 P4 禁静默降级）。"""
        scored = _load_scored()
        seen_models: list[str] = []

        def fake_post(*args, **kwargs):
            model = kwargs["json"]["model"]
            seen_models.append(model)
            if model == syn._DEFAULT_SYNTHESIS_MODEL:
                raise _status_error(429, "rate limited")
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {
                "choices": [{"message": {"content": _valid_model_output(scored)}}]
            }
            return resp

        monkeypatch.setattr("httpx.post", fake_post)
        monkeypatch.setattr("time.sleep", lambda s: None)

        result = syn.synthesize(scored, date="2026-08-02")

        assert result["focus_discussions"][0]["topic"] == "智能体长记忆基础设施"
        assert _FAKE_BRIEFING_FINGERPRINT not in json.dumps(result, ensure_ascii=False)
        assert seen_models[-1] == syn._FALLBACK_SYNTHESIS_MODEL
        out = capsys.readouterr().out
        assert f"SYNTHESIS_FALLBACK model={syn._FALLBACK_SYNTHESIS_MODEL}" in out

    def test_env_flag_starts_from_fallback_family(self, monkeypatch, capsys):
        """契约 1：AKB2_LLM_FAMILY_FALLBACK=1 → 首次请求就打备族，主族零请求。"""
        scored = _load_scored()
        seen_models: list[str] = []
        monkeypatch.setenv(syn._FAMILY_FALLBACK_ENV, "1")

        def fake_post(*args, **kwargs):
            seen_models.append(kwargs["json"]["model"])
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {
                "choices": [{"message": {"content": _valid_model_output(scored)}}]
            }
            return resp

        monkeypatch.setattr("httpx.post", fake_post)

        syn.synthesize(scored, date="2026-08-02")

        assert seen_models == [syn._FALLBACK_SYNTHESIS_MODEL], seen_models
        assert syn._DEFAULT_SYNTHESIS_MODEL not in seen_models
        assert f"reason=env_{syn._FAMILY_FALLBACK_ENV}=1" in capsys.readouterr().out

    def test_both_families_down_reports_both_causes(self, monkeypatch):
        """两族都挂时两族真因都要可见（P4 失败可见），不是只留末次错误。"""
        monkeypatch.setattr("httpx.post", _raising_post(_status_error(429, "rate limited")))
        monkeypatch.setattr("time.sleep", lambda s: None)

        with pytest.raises(syn.SynthesisUnavailableError) as exc:
            syn._call_deepseek("prompt")

        msg = str(exc.value)
        assert syn._DEFAULT_SYNTHESIS_MODEL in msg
        assert syn._FALLBACK_SYNTHESIS_MODEL in msg

    def test_families_are_distinct(self):
        """主备同族 = 没有降级余量（宪法 P7 异族兜底）。"""
        assert syn._DEFAULT_SYNTHESIS_MODEL != syn._FALLBACK_SYNTHESIS_MODEL

    def test_quality_retries_do_not_re_hit_a_family_proven_dead(self, monkeypatch, capsys):
        """主族判定不可用后，后续质量重试轮**不得**再打主族。

        `synthesize()` 对质量退化重试 3 轮，每轮重新调 `_call_deepseek`。不记账的话
        已死的主族会被打满 3 轮（每轮内部还有 `_call_llm` 的 2 次重试）——
        「明知耗尽还硬试」换个位置复发，且在共享 Gateway 上拖累别的链路。
        """
        scored = _load_scored()
        top = sorted(scored["tweets"], key=lambda t: t.get("score", 0), reverse=True)
        ids = [str(t["id"]) for t in top[:3]]
        degraded = json.dumps(
            {"focus_discussions": [{"topic": "退化主题", "supporting_tweet_ids": ids}]}
        )
        fallback_payloads = [degraded, _valid_model_output(scored)]
        seen_models: list[str] = []

        def fake_post(*args, **kwargs):
            model = kwargs["json"]["model"]
            seen_models.append(model)
            if model == syn._DEFAULT_SYNTHESIS_MODEL:
                raise _status_error(429, "rate limited")
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {
                "choices": [{"message": {"content": fallback_payloads.pop(0)}}]
            }
            return resp

        monkeypatch.setattr("httpx.post", fake_post)
        monkeypatch.setattr("time.sleep", lambda s: None)

        result = syn.synthesize(scored, date="2026-08-02")

        assert result["focus_discussions"][0]["topic"] == "智能体长记忆基础设施"
        primary_hits = seen_models.count(syn._DEFAULT_SYNTHESIS_MODEL)
        # 第 1 轮打满 _call_llm 的 max_retries+1 次后判死；第 2 轮起零次
        assert primary_hits == 3, (
            f"主族判死后仍被后续质量重试轮打了（共 {primary_hits} 次）: {seen_models}"
        )
        assert "reason=primary_already_known_unavailable" in capsys.readouterr().out

    def test_family_ledger_is_reset_between_synthesize_calls(self, monkeypatch):
        """闩的生命周期是单次 synthesize —— 否则第二次运行会永久跳过主族。"""
        scored = _load_scored()
        syn._unavailable_families.add(syn._DEFAULT_SYNTHESIS_MODEL)
        seen_models: list[str] = []

        def fake_post(*args, **kwargs):
            seen_models.append(kwargs["json"]["model"])
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {
                "choices": [{"message": {"content": _valid_model_output(scored)}}]
            }
            return resp

        monkeypatch.setattr("httpx.post", fake_post)

        syn.synthesize(scored, date="2026-08-02")

        assert seen_models == [syn._DEFAULT_SYNTHESIS_MODEL], (
            f"上一次运行的族账本泄漏到本次: {seen_models}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# SC-R1-06 / SC-R1-08（social 侧对席）—— 主链是共享闸生产者；账户级预算不换族
# ═══════════════════════════════════════════════════════════════════════════

# 2026-08-02 生产实测的真实预算耗尽响应体（逐字）
_BUDGET_BODY_UTC_DAILY = (
    "ExceededBudget: User=zhuzan exceeded UTC daily cap. "
    "Date=2026-08-01, Spend=2701.228476741798, DailyCap=2700.0"
)


class TestSharedBudgetGateProducer:
    def test_budget_429_writes_shared_gate_and_skips_fallback(
        self, monkeypatch, tmp_path, capsys,
    ):
        """social 主链撞预算 429：布共享闸 + 首击即停 + 备族零请求。

        R1 实测旧行为：对真实 UTC 日预算体发出 DeepSeek×3 + Gemini×3 共 6 次请求才失败，
        且**不留任何跨进程状态** —— 后面每个入口都得自己再撞一次 HTTP。
        """
        import datetime as dt

        from akb2.common import budget_gate

        monkeypatch.setenv("AKB2_STATE_ROOT", str(tmp_path / "state"))
        monkeypatch.setattr("time.sleep", lambda s: None)
        seen_models: list[str] = []

        def fake_post(*args, **kwargs):
            seen_models.append(kwargs["json"]["model"])
            raise _status_error(429, _BUDGET_BODY_UTC_DAILY)

        monkeypatch.setattr("httpx.post", fake_post)

        with pytest.raises(syn.SynthesisBudgetExceededError, match="预算耗尽"):
            syn._call_deepseek("prompt")

        assert seen_models == [syn._DEFAULT_SYNTHESIS_MODEL], (
            f"预算耗尽应首击即停且不换族，实际请求序列: {seen_models}"
        )
        assert syn._FALLBACK_SYNTHESIS_MODEL not in seen_models
        assert "SYNTHESIS_FALLBACK" not in capsys.readouterr().out

        block = budget_gate.load_state_snapshot()["active_block"]
        assert block is not None, "撞到预算 429 却没给后续入口布闸"
        assert block["budget_scope"] == budget_gate.UTC_DAILY_SCOPE
        assert block["consumer"] == syn.BUDGET_GATE_CONSUMER
        decision = budget_gate.query_budget_gate(
            now=dt.datetime(2026, 8, 1, 23, 30, tzinfo=dt.UTC), consumer="github.synthesize",
        )
        assert decision.allowed is False

    def test_budget_exceeded_is_not_retried_by_quality_loop(self, monkeypatch, tmp_path):
        """synthesize() 的 3 轮质量重试对预算耗尽必须立即上抛（宪法 P2 收敛优先）。"""
        monkeypatch.setenv("AKB2_STATE_ROOT", str(tmp_path / "state"))
        monkeypatch.setattr("time.sleep", lambda s: None)
        scored = _load_scored()
        posts: list[str] = []

        def fake_post(*args, **kwargs):
            posts.append(kwargs["json"]["model"])
            raise _status_error(429, _BUDGET_BODY_UTC_DAILY)

        monkeypatch.setattr("httpx.post", fake_post)

        with pytest.raises(syn.SynthesisUnavailableError):
            syn.synthesize(scored, date="2026-08-02")

        assert len(posts) == 1, f"预算耗尽仍被质量重试循环放大成 {len(posts)} 次请求: {posts}"

    def test_transient_rate_limit_still_retries_and_falls_back(self, monkeypatch, tmp_path):
        """反向守卫：不含预算字样的 429 仍必须退避重试并换族（别把修复做成"永不重试"）。"""
        monkeypatch.setenv("AKB2_STATE_ROOT", str(tmp_path / "state"))
        monkeypatch.setattr("time.sleep", lambda s: None)
        scored = _load_scored()
        seen_models: list[str] = []

        def fake_post(*args, **kwargs):
            model = kwargs["json"]["model"]
            seen_models.append(model)
            if model == syn._DEFAULT_SYNTHESIS_MODEL:
                raise _status_error(429, '{"error": {"message": "Too many requests"}}')
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {
                "choices": [{"message": {"content": _valid_model_output(scored)}}]
            }
            return resp

        monkeypatch.setattr("httpx.post", fake_post)

        result = syn.synthesize(scored, date="2026-08-02")

        assert result["focus_discussions"][0]["topic"] == "智能体长记忆基础设施"
        assert seen_models.count(syn._DEFAULT_SYNTHESIS_MODEL) == 3, seen_models
        assert seen_models[-1] == syn._FALLBACK_SYNTHESIS_MODEL

        from akb2.common import budget_gate

        assert budget_gate.load_state_snapshot()["active_block"] is None, (
            "瞬时限流被误当成预算耗尽布闸了 —— 那会让整机所有链路一起被拦"
        )


class TestSynthesisReasoningEffort:
    def test_primary_request_sends_max_reasoning_effort(self, monkeypatch):
        """主族默认 flash + max；只改模型名却不传档位，等于没换成 max。"""
        scored = _load_scored()
        payloads: list[dict] = []

        def fake_post(*args, **kwargs):
            payloads.append(kwargs["json"])
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {
                "choices": [{"message": {"content": _valid_model_output(scored)}}]
            }
            return resp

        monkeypatch.setattr("httpx.post", fake_post)
        syn.synthesize(scored, date="2026-08-02")

        assert payloads, "主族请求没发出去"
        body = payloads[0]
        assert body["model"] == "deepseek/deepseek-v4-flash"
        assert body["reasoning_effort"] == "max"

    def test_fallback_request_does_not_inherit_primary_effort(self, monkeypatch):
        """备族 Gemini 不套主族 max，避免把 DeepSeek 档位误加到另一家。"""
        scored = _load_scored()
        monkeypatch.setenv(syn._FAMILY_FALLBACK_ENV, "1")
        payloads: list[dict] = []

        def fake_post(*args, **kwargs):
            payloads.append(kwargs["json"])
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {
                "choices": [{"message": {"content": _valid_model_output(scored)}}]
            }
            return resp

        monkeypatch.setattr("httpx.post", fake_post)
        syn.synthesize(scored, date="2026-08-02")

        assert len(payloads) == 1
        assert payloads[0]["model"] == syn._FALLBACK_SYNTHESIS_MODEL
        assert "reasoning_effort" not in payloads[0]
