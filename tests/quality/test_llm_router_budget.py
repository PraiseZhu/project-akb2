from __future__ import annotations

import ast
import datetime as dt
import inspect
import json
import sys
import textwrap
import traceback
import types

import pytest

from src.akb2.common import budget_gate, http_retry
from src.akb2.common.state_paths import LEDGER_SUBDIR, resolve_state_file
from src.akb2.quality import llm_router
from src.akb2.quality.llm_router import LLMProviderError, route_llm

# Gateway 预算耗尽的两种真实响应体（2026-08-01/08-02 生产实测原文，逐字保留）。
# 形态 A 是 R1 审查复现的漏判源：它**不含** "total cap"/"total budget" 字样，
# llm_router 旧的本地解析器因此归出 scope=''，闸门永不短路。
FORM_A_TOTAL_BODY = (
    "ExceededBudget: User=zhuzan over budget. "
    "Spend=18000.62608448676, Budget=18000.0"
)
FORM_B_UTC_DAILY_BODY = (
    "ExceededBudget: User=zhuzan exceeded UTC daily cap. "
    "Date=2026-08-01, Spend=2701.228476741798, DailyCap=2700.0"
)


def _install_fake_openai(monkeypatch, create_fn):
    class _FakeCompletions:
        def create(self, **kwargs):
            return create_fn(**kwargs)

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=_FakeCompletions())

    class _FakeAPIError(RuntimeError):
        def __init__(self, message: str, *, status_code: int, body):
            super().__init__(message)
            self.message = message
            self.status_code = status_code
            self.body = body

    fake_openai = types.SimpleNamespace(
        OpenAI=_FakeOpenAI,
        APITimeoutError=TimeoutError,
        APIError=_FakeAPIError,
    )
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    return _FakeAPIError


@pytest.mark.usefixtures("_isolate_quality_state")
def test_budget_gate_short_circuits_all_consumers_without_real_http(
    monkeypatch, caplog
):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "gw-test-key")
    monkeypatch.setenv("AKB2_BUDGET_GATE_NOW", "2026-08-01T21:30:00Z")
    budget_gate.record_budget_event(
        budget_gate.BudgetDiagnostics(
            error_type=budget_gate.BUDGET_EXCEEDED,
            budget_scope=budget_gate.UTC_DAILY_SCOPE,
            spend=2701.23,
            cap=2700.0,
            budget_date="2026-08-01",
            provider_code=budget_gate.PROVIDER_CODE_429,
            raw_message="ExceededBudget: exceeded UTC daily cap.",
        ),
        recorded_at=dt.datetime(2026, 8, 1, 21, 14, tzinfo=dt.UTC),
        consumer="seed",
    )

    create_calls: list[dict[str, object]] = []

    def _unexpected_create(**kwargs):
        create_calls.append(kwargs)
        raise AssertionError("budget gate should short-circuit before HTTP")

    _install_fake_openai(monkeypatch, _unexpected_create)

    for consumer in (
        "github.synthesize",
        "quality.admission",
        "quality.distill",
    ):
        with pytest.raises(LLMProviderError, match="blocked_by_shared_budget"):
            route_llm(
                "deepseek-v4-pro",
                [{"role": "user", "content": "test"}],
                budget_consumer=consumer,
            )

    assert create_calls == []
    assert "consumer=github.synthesize" in caplog.text
    assert "consumer=quality.admission" in caplog.text
    assert "consumer=quality.distill" in caplog.text


@pytest.mark.usefixtures("_isolate_quality_state")
def test_llm_router_records_structured_budget_diagnostics(monkeypatch):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "gw-test-key")

    def _raise_429(**kwargs):
        error_body = {
            "error": {
                "message": (
                    "ExceededBudget: exceeded UTC daily cap. "
                    "Date=2026-08-01, Spend=2701.23, DailyCap=2700.0"
                )
            }
        }
        raise api_error(
            error_body["error"]["message"],
            status_code=429,
            body=error_body,
        )

    api_error = _install_fake_openai(monkeypatch, _raise_429)

    with pytest.raises(LLMProviderError, match="LLM API 错误"):
        route_llm(
            "deepseek-v4-pro",
            [{"role": "user", "content": "test"}],
            budget_consumer="quality.admission",
        )

    ledger_path = resolve_state_file("quality-ledger.jsonl", subdir=LEDGER_SUBDIR)
    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[-1])

    assert record["provider_code"] == "429"
    assert record["error_type"] == "budget_exceeded"
    assert record["budget_scope"] == "utc_daily"
    assert record["spend"] == 2701.23
    assert record["cap"] == 2700.0

    state = budget_gate.load_state_snapshot()
    assert state["last_error"]["error_type"] == "budget_exceeded"
    assert state["last_error"]["budget_scope"] == "utc_daily"


# ---------------------------------------------------------------------------
# SC-R1-01 —— 429 两形态由**单一**分类器一致映射为具名 scope
# ---------------------------------------------------------------------------


def _executable_source(obj) -> str:
    """对象的可执行代码（丢掉注释与 docstring）。

    静态「唯一 owner」断言必须只看代码：注释里出现 "total cap" 是**在解释**这条
    缺陷，不是在实现它；不剥注释的断言会把正确的修复也判红（本轮实测踩过一次）。
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(obj)))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            body.pop(0)
    return ast.unparse(tree)


def _route_and_capture_budget_state(monkeypatch, body_text: str) -> dict[str, object]:
    """走完整 route_llm → record_budget_event 真实路径，返回落盘后的封锁槽。"""
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "gw-test-key")

    def _raise_429(**kwargs):
        raise api_error(body_text, status_code=429, body={"error": {"message": body_text}})

    api_error = _install_fake_openai(monkeypatch, _raise_429)

    with pytest.raises(LLMProviderError):
        route_llm(
            "deepseek-v4-pro",
            [{"role": "user", "content": "test"}],
            budget_consumer="quality.distill",
        )
    return budget_gate.load_state_snapshot()


@pytest.mark.usefixtures("_isolate_quality_state")
def test_total_form_classified_via_single_shared_classifier(monkeypatch):
    """形态 A（total）经 llm_router 真实路径必须落成 scope='total' 并真的短路。

    绑定的可观测行为有三层，缺一层都能让这条缺陷复发：
      ① 落盘的 active_block.budget_scope == 'total'（旧代码是 ''）
      ② query_budget_gate 真的 allowed=False（旧代码 allowed=True
         reason='budget_cooldown_expired' —— 连归因都是误导的）
      ③ 全仓只有一处 429 分类实现：llm_router 委派给 http_retry，不再自带解析器
    """
    monkeypatch.setenv("AKB2_BUDGET_GATE_NOW", "2026-08-01T21:30:00Z")
    state = _route_and_capture_budget_state(monkeypatch, FORM_A_TOTAL_BODY)

    block = state["active_block"]
    assert block["error_type"] == budget_gate.BUDGET_EXCEEDED
    assert block["budget_scope"] == budget_gate.TOTAL_SCOPE, (
        "形态 A 必须归出 total scope；空 scope 会让 cooldown 算不出来"
    )
    assert block["cap"] == 18000.0
    assert block["spend"] == 18000.62608448676
    assert block["cooldown_until"], "total scope 必须有具名 cooldown 边界"

    decision = budget_gate.query_budget_gate(
        now=dt.datetime(2026, 8, 1, 21, 40, tzinfo=dt.UTC),
        consumer="github.synthesize",
    )
    assert decision.allowed is False
    assert decision.reason == "total_budget_exhausted"

    # 唯一 owner：llm_router 不得再自带 429 文本解析。
    # 比对的是**去掉注释与 docstring 的可执行代码**（ast.unparse 只留代码）——
    # 否则解释这条缺陷的注释本身会把断言打红。
    router_code = _executable_source(llm_router._diagnostics_from_provider_error)
    assert "classify_provider_error" in router_code
    for drifted_marker in ("total cap", "total budget", "re.compile", "DailyCap", "lower()"):
        assert drifted_marker not in router_code, (
            f"llm_router 又长出了本地 429 解析（命中 {drifted_marker!r}）——"
            "分类必须只有 http_retry 一处实现"
        )
    # 模块级也不许残留预算解析用的正则常量
    module_code = _executable_source(llm_router)
    assert "DailyCap" not in module_code
    assert "TotalCap" not in module_code


@pytest.mark.usefixtures("_isolate_quality_state")
def test_utc_daily_form_still_classified_via_shared_classifier(monkeypatch):
    """形态 B（utc_daily）在统一分类器下判定不回退（防修 A 打坏 B）。"""
    monkeypatch.setenv("AKB2_BUDGET_GATE_NOW", "2026-08-01T21:30:00Z")
    state = _route_and_capture_budget_state(monkeypatch, FORM_B_UTC_DAILY_BODY)

    block = state["active_block"]
    assert block["budget_scope"] == budget_gate.UTC_DAILY_SCOPE
    assert block["budget_date"] == "2026-08-01"
    assert block["cap"] == 2700.0

    decision = budget_gate.query_budget_gate(
        now=dt.datetime(2026, 8, 1, 23, 59, tzinfo=dt.UTC),
        consumer="github.synthesize",
    )
    assert decision.allowed is False
    assert decision.reason == "utc_daily_budget_exhausted"


@pytest.mark.usefixtures("_isolate_quality_state")
def test_provider_raw_text_is_redacted_before_ledger_and_exception(monkeypatch):
    """SC-R1-10 的 llm_router 侧：凭证样式与账户身份不得进账本 / 异常文本。"""
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "gw-test-key")
    sentinel_body = (
        "ExceededBudget: User=zhuzan over budget. Spend=1.0, Budget=1.0 "
        "(auth used api_key=sk-probe-secret-2026 via Bearer sk-probe-secret-2026)"
    )

    def _raise_429(**kwargs):
        raise api_error(sentinel_body, status_code=429, body=sentinel_body)

    api_error = _install_fake_openai(monkeypatch, _raise_429)

    with pytest.raises(LLMProviderError) as excinfo:
        route_llm(
            "deepseek-v4-pro",
            [{"role": "user", "content": "test"}],
            budget_consumer="quality.distill",
        )

    assert "sk-probe-secret-2026" not in str(excinfo.value)
    assert "User=zhuzan" not in str(excinfo.value)

    ledger_text = resolve_state_file(
        "quality-ledger.jsonl", subdir=LEDGER_SUBDIR
    ).read_text(encoding="utf-8")
    assert "sk-probe-secret-2026" not in ledger_text
    assert "User=zhuzan" not in ledger_text
    # 分类证据必须保留（脱敏不能把诊断价值一起删掉）
    record = json.loads(ledger_text.splitlines()[-1])
    assert record["error_type"] == "budget_exceeded"
    assert record["budget_scope"] == "total"

    state_text = budget_gate._state_path().read_text(encoding="utf-8")
    assert "sk-probe-secret-2026" not in state_text
    assert "User=zhuzan" not in state_text


@pytest.mark.usefixtures("_isolate_quality_state")
def test_traceback_chain_has_no_raw_provider_text(monkeypatch):
    """SC-R2-11①：脱敏必须同时覆盖 str(err) 与 traceback 全链，不能只覆盖一条。

    上一版代码公开文本已脱敏（用 diagnostics.raw_message 而非 str(exc)），但结尾
    `from exc` 把**未脱敏的 provider 原始异常**留在 `__cause__` 里 ——
    `traceback.format_exception()` 会把整条链打印出来。R2 对抗席用含伪凭证哨兵的
    探针实测：`outer_secret=False` 但 `cause_secret=True` / `traceback_secret=True`。
    只覆盖两条打印路径中的一条，那不叫脱敏。

    哨兵串只在变量里出现一次，不写进任何会进 traceback 的源码行（否则打印出来的
    源码行本身会把断言打红，那是假红）。
    """
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "gw-test-key")
    sentinel = "sk-" + "probe-traceback-2026"
    sentinel_body = (
        "ExceededBudget: User=zhuzan over budget. Spend=1.0, Budget=1.0 "
        f"(auth used api_key={sentinel} via Bearer {sentinel})"
    )

    def _raise_429(**kwargs):
        raise api_error(sentinel_body, status_code=429, body=sentinel_body)

    api_error = _install_fake_openai(monkeypatch, _raise_429)

    with pytest.raises(LLMProviderError) as excinfo:
        route_llm(
            "deepseek-v4-pro",
            [{"role": "user", "content": "test"}],
            budget_consumer="quality.distill",
        )

    err = excinfo.value
    rendered = "".join(
        traceback.format_exception(type(err), err, err.__traceback__)
    )
    assert sentinel not in rendered, (
        "traceback 全链仍含未脱敏 provider 原文——公开文本脱敏而链上没脱敏等于没脱敏"
    )
    assert "User=zhuzan" not in rendered

    # 结构性锁：链上不得再挂 provider 异常，且不许靠 __context__ 复活
    # （`from None` 同时清 __cause__ 并置 __suppress_context__=True）。
    assert err.__cause__ is None
    assert err.__suppress_context__ is True

    # 脱敏不能把诊断价值一起删掉：provider 异常类型名 + 结构化归因必须还在。
    # 类型名是 runner._INFRA_ERROR_MARKERS 的判据之一（"BadRequestError" 归 infra）。
    assert "_FakeAPIError" in str(err)
    assert "error_type=budget_exceeded" in str(err)
    assert "budget_scope=total" in str(err)


def test_deepseek_direct_routes_do_not_leak_raw_provider_text(monkeypatch):
    """SC-R2-11① 的审计连带面：两条 DeepSeek 直连路由此前把 `{exc}` 直接内插公开消息。

    比被点名的 route_llm 那处更露（那处至少用了脱敏字段），所以一并收口：公开文本
    与 traceback 全链都不得含哨兵，异常类型名保留。
    """
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "gw-test-key")
    monkeypatch.setenv("DEEPSEEK_BETA_API_KEY", "ds-beta-test-key")
    sentinel = "sk-" + "probe-direct-2026"
    sentinel_body = f"Error code: 400 - upstream rejected (api_key={sentinel})"

    def _raise_400(**kwargs):
        raise api_error(sentinel_body, status_code=400, body=sentinel_body)

    api_error = _install_fake_openai(monkeypatch, _raise_400)

    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    for call in (
        lambda: llm_router.route_deepseek_beta_tool(
            [{"role": "user", "content": "test"}], schema
        ),
        lambda: llm_router.route_deepseek_json_object(
            [{"role": "user", "content": "test"}]
        ),
    ):
        with pytest.raises(LLMProviderError) as excinfo:
            call()
        err = excinfo.value
        rendered = "".join(
            traceback.format_exception(type(err), err, err.__traceback__)
        )
        assert sentinel not in str(err)
        assert sentinel not in rendered
        assert err.__cause__ is None
        assert err.__suppress_context__ is True
        # 上游 400 的 infra 指纹不能被脱敏顺手删掉（F39 重审判据）
        assert "Error code: 400" in str(err)


# ---------------------------------------------------------------------------
# SC-R4-03 ——「凭证键名」规则必须独立成立，不许靠前缀规则代偿
# ---------------------------------------------------------------------------
# 哨兵刻意**不含任何已知前缀**（无 `sk-` / `ghp_` / `AIza` / `xox` / `Bearer`）：
# 上一轮的测试用的是 `api_key=sk-probe-secret-2026`，它能通过是因为前面那条 `sk-*`
# **前缀规则**先把值吃掉了 —— 键名规则整条失配（引号包裹时 `\b` 后紧跟 `"` 而非 `[=:]`）
# 却没有任何测试变红。用不透明哨兵之后，只有键名规则能救它，掩盖不再可能。
_OPAQUE_SENTINEL = "opaque-cred-sentinel-9f3a2b71"

# 三种真实形态（provider 响应体是 JSON、异常 repr 是 Python dict、query/env 是裸 kv）
_QUOTED_KEY_FORMS = (
    ("bare-kv", "%(k)s=%(v)s"),
    ("json-double-quoted", '{"%(k)s":"%(v)s"}'),
    ("json-spaced", '{"%(k)s": "%(v)s"}'),
    ("python-repr-single-quoted", "{'%(k)s': '%(v)s'}"),
    ("yaml-ish-colon", "%(k)s: '%(v)s'"),
)
_CRED_KEYS = ("api_key", "api-key", "access_token", "auth_token", "token", "secret", "password")


def test_redactor_matches_quoted_json_keys():
    """引号包裹的凭证键名必须被脱敏，且不得依赖前缀规则代偿。"""
    # 前提断言：哨兵单独出现时**原样穿透** —— 证明下面每一个 pass 都是键名规则的功劳。
    # 若哪天有人给哨兵加了个 `sk-` 前缀，这条会先红，掩盖回归被挡在门口。
    assert http_retry.redact_provider_text(_OPAQUE_SENTINEL) == _OPAQUE_SENTINEL, (
        "哨兵本身被某条前缀规则命中了 —— 它就无法证明键名规则是否工作，请换一个不透明哨兵"
    )

    leaked: list[str] = []
    for key in _CRED_KEYS:
        for form_name, template in _QUOTED_KEY_FORMS:
            raw = template % {"k": key, "v": _OPAQUE_SENTINEL}
            out = http_retry.redact_provider_text(raw)
            if _OPAQUE_SENTINEL in out:
                leaked.append(f"{key}/{form_name}: {raw!r} -> {out!r}")
            elif http_retry.REDACTED_PLACEHOLDER not in out:
                leaked.append(f"{key}/{form_name}: 值消失但无掩码标记 -> {out!r}")
    assert not leaked, "以下形态未被脱敏（键名规则漏了引号包裹形态）:\n" + "\n".join(leaked)

    # 账户身份 PII 走同一口径：`User=` 与 `{"User":"…"}` 都得掩掉，不许只修一半。
    for raw in (
        f"User={_OPAQUE_SENTINEL}",
        f'{{"User":"{_OPAQUE_SENTINEL}"}}',
        f"{{'User': '{_OPAQUE_SENTINEL}'}}",
    ):
        assert _OPAQUE_SENTINEL not in http_retry.redact_provider_text(raw), raw

    # 脱敏不得吃掉 429 归因判据（宪法 P4：失败可见 —— 掩码后仍要能算出 scope/spend/cap）。
    diagnosis = http_retry.classify_provider_error(429, FORM_A_TOTAL_BODY)
    assert diagnosis.error_type == http_retry.ERROR_TYPE_BUDGET_EXCEEDED
    assert diagnosis.budget_scope == http_retry.BUDGET_SCOPE_TOTAL
    assert (diagnosis.spend, diagnosis.cap) == (18000.62608448676, 18000.0)
    assert "zhuzan" not in diagnosis.raw_excerpt


# ---------------------------------------------------------------------------
# SC-R5-04 —— 下划线复合键的**前导**词边界
# ---------------------------------------------------------------------------
# `_` 是词字符，所以 `re.search(r"\bsecret\b", "client_secret")` 为 **False**：前导 `\b`
# 让下划线复合键整条规则失配，`client_secret=` / `refresh_token=` 原文穿透 —— 而规则里
# 明明列了 `secret` / `token`。
#
# 这是同一不变量的第三次表现，前两次分别是 R2（缺 redact 调用）与 R4（尾部引号边界）。
# 教训记在实现侧注释里：R4 声称「物理上不可能只补一半」，但共享片段只覆盖**引号**这一个
# 轴，管不到**词边界**这另一个轴 —— 断言的范围写宽了，缺口就活到了下一轮。
_COMPOUND_SENTINEL = "opaque-compound-sentinel-4c7e13a9"

# 真实 OAuth / gateway 键名：下划线复合，词尾才是 credential 词
_COMPOUND_KEYS = (
    "client_secret",
    "refresh_token",
    "client_access_token",
    "oauth_token",
    "app_secret",
)
_COMPOUND_FORMS = (
    ("bare-kv", "%(k)s=%(v)s"),
    ("url-query", "?a=1&%(k)s=%(v)s"),
    ("json-double-quoted", '{"%(k)s":"%(v)s"}'),
    ("json-spaced", '{"%(k)s": "%(v)s"}'),
    ("python-repr-single-quoted", "{'%(k)s': '%(v)s'}"),
)


def test_redactor_masks_underscore_compound_keys():
    """下划线复合键必须命中词尾 credential 词，前导边界不得用 `\\b`。"""
    # 防掩盖前提（沿用 R4 的闸）：哨兵单独出现时原样穿透 —— 证明下面的 pass 全是键名
    # 规则的功劳，而不是某条前缀规则代偿。
    assert http_retry.redact_provider_text(_COMPOUND_SENTINEL) == _COMPOUND_SENTINEL, (
        "哨兵本身被某条前缀规则命中了 —— 它就无法证明键名规则是否工作，请换一个不透明哨兵"
    )

    leaked: list[str] = []
    for key in _COMPOUND_KEYS:
        for form_name, template in _COMPOUND_FORMS:
            raw = template % {"k": key, "v": _COMPOUND_SENTINEL}
            out = http_retry.redact_provider_text(raw)
            if _COMPOUND_SENTINEL in out:
                leaked.append(f"{key}/{form_name}: {raw!r} -> {out!r}")
            elif http_retry.REDACTED_PLACEHOLDER not in out:
                leaked.append(f"{key}/{form_name}: 值消失但无掩码标记 -> {out!r}")
    assert not leaked, (
        "以下下划线复合键未被脱敏（前导 `\\b` 把 `_` 当词字符 → 整条失配）:\n"
        + "\n".join(leaked)
    )

    # 字母直连的取舍在 R6 精化（SC-R6-04）：可完整切进凭证词汇的直连**收**
    # （`secretkey`→secret+key，见 test_credential_key_matching_covers_all_axes），
    # 不可切的直连仍**不收** —— `mysecret` 的 "my" 不在词汇表，钉住防止误掩普通标识符。
    raw = f"mysecret={_COMPOUND_SENTINEL}"
    assert http_retry.redact_provider_text(raw) == raw, (
        "mysecret 被命中了 —— token 分解放得过宽，普通标识符会被误掩"
    )

    # 账户身份规则用**同一个**前导片段，不许两条规则各修一半（这正是本条 SC 的失败形状）。
    # `_User=` 是 `\b` 与 `(?<![A-Za-z0-9])` 唯一的行为差异点 —— 没有这条断言，
    # 把 User 规则的前导改回 `\b` 不会让任何测试变红，那个改动就成了没有牙的装饰。
    for raw in (f"account_User={_COMPOUND_SENTINEL}", f'{{"account_User":"{_COMPOUND_SENTINEL}"}}'):
        assert _COMPOUND_SENTINEL not in http_retry.redact_provider_text(raw), raw


def test_redact_provider_text_is_idempotent_and_bounded():
    once = http_retry.redact_provider_text(
        "User=zhuzan token=abc123 Bearer sk-live-xyz " + "x" * 400
    )
    twice = http_retry.redact_provider_text(once)
    assert once == twice
    assert "zhuzan" not in once
    assert "sk-live-xyz" not in once
    assert once.endswith("…<truncated>")
    assert len(once) == http_retry.PROVIDER_TEXT_MAX_CHARS + len("…<truncated>")


# ---------------------------------------------------------------------------
# SC-R6-04 —— 键名匹配按 token 分解，一次覆盖全部词法轴
# ---------------------------------------------------------------------------
# 同一不变量的第四次表现（R2 缺调用 / R4 引号轴 / R5 下划线轴 / R6 驼峰轴 clientSecret）
# 之后，实现从「regex 逐轴补」换成 token 分解：分隔符（_ - . 空格）+ 驼峰边界切 token，
# 语义由**尾 token（头名词）**判定 —— `client_secret` 头名词是 secret（值是凭证），
# `secret_id` 头名词是 id（值是标识符），"任一 token 含 secret" 无法同时满足这两侧。
_AXES_SENTINEL = "opaque-axes-sentinel-6e2d90c7"

# 应命中：下划线 / 驼峰 / 直连可分解 / 连字符 / 全大写，五个词法轴各有代表
_AXES_HIT_KEYS = (
    "client_secret",
    "clientSecret",
    "refresh_token",
    "refreshToken",
    "secretkey",
    "secret-key",
    "API_KEY",
)
# 不应命中：凭证词在修饰位（头名词是 id/hash）、普通标识符、不可分解直连
_AXES_MISS_KEYS = (
    "secret_id",
    "token_id",
    "user_id",
    "username",
    "content_hash",
    "mysecret",
)
_AXES_FORMS = (
    ("bare-kv", "%(k)s=%(v)s"),
    ("url-query", "?a=1&%(k)s=%(v)s"),
    ("json-double-quoted", '{"%(k)s":"%(v)s"}'),
    ("python-repr-single-quoted", "{'%(k)s': '%(v)s'}"),
)


def test_credential_key_matching_covers_all_axes():
    """token 分解后：全部词法轴的凭证键命中，标识符类键与不可分解直连不命中。"""
    # 防掩盖前提（沿用 R4/R5 的闸）：哨兵单独出现时原样穿透。
    assert http_retry.redact_provider_text(_AXES_SENTINEL) == _AXES_SENTINEL, (
        "哨兵被前缀规则命中 —— 无法证明键名规则是否工作，请换一个不透明哨兵"
    )

    leaked: list[str] = []
    for key in _AXES_HIT_KEYS:
        for form_name, template in _AXES_FORMS:
            raw = template % {"k": key, "v": _AXES_SENTINEL}
            out = http_retry.redact_provider_text(raw)
            if _AXES_SENTINEL in out:
                leaked.append(f"{key}/{form_name}: {raw!r} -> {out!r}")
            elif http_retry.REDACTED_PLACEHOLDER not in out:
                leaked.append(f"{key}/{form_name}: 值消失但无掩码标记 -> {out!r}")
    assert not leaked, "以下凭证键词法轴未被脱敏:\n" + "\n".join(leaked)

    over_masked: list[str] = []
    for key in _AXES_MISS_KEYS:
        raw = f"{key}={_AXES_SENTINEL}"
        out = http_retry.redact_provider_text(raw)
        if out != raw:
            over_masked.append(f"{key}: {raw!r} -> {out!r}")
    assert not over_masked, (
        "以下非凭证键被误掩（token 分解的位置语义被破坏）:\n" + "\n".join(over_masked)
    )

    # 既有 `User` 账户身份掩码不得退化（R5 upstream 19 样本 diff 确认 R4 起即掩码）。
    assert "zhuzan" not in http_retry.redact_provider_text("User=zhuzan over budget.")


# ---------------------------------------------------------------------------
# SC-R7-01 —— 凭证值跨分隔符必须**整段**掩码，不留明文尾巴
# ---------------------------------------------------------------------------
# R6 把 `&` / `?` 无条件排除在值字符类之外（为的是让 query string 里相邻的键各自归位）。
# 副作用：凭证值**自身**含 `&`/`?` 时，掩码只到第一个分隔符为止 —— `password=p@ss&word!`
# 落成 `password=<redacted>word!`，`token=abc&def` 落成 `token=<redacted>def`。
# 掩一半漏一半比完全不掩更危险：输出看起来已经脱敏，明文尾巴却照样进日志/账本。
#
# 值侧的判据从此不再是字符黑名单，而是语义条件「后面像不像另一个 kv 键」（实现侧
# `_NEXT_KV_AHEAD`）。本测试钉住方向 ①（整段掩掉）；方向 ②（相邻键仍分家）由
# test_query_string_keys_still_split_at_ampersand 钉住，两者缺一即为回归。
_AMP_HEAD_SENTINEL = "opaque-amp-head-2f7b41c9"
_AMP_TAIL_SENTINEL = "opaque-amp-tail-8c3e05d6"

# 分隔符出现在值内部的四种真实形态（& / ? / 连续多个 / 尾部标点）
_AMP_VALUES = (
    ("ampersand", f"{_AMP_HEAD_SENTINEL}&{_AMP_TAIL_SENTINEL}"),
    ("question-mark", f"{_AMP_HEAD_SENTINEL}?{_AMP_TAIL_SENTINEL}"),
    ("multi-separator", f"{_AMP_HEAD_SENTINEL}&x?{_AMP_TAIL_SENTINEL}"),
    ("trailing-punct", f"{_AMP_HEAD_SENTINEL}&{_AMP_TAIL_SENTINEL}!"),
)
_AMP_FORMS = (
    ("bare-kv", "%(k)s=%(v)s"),
    ("url-query", "?a=1&%(k)s=%(v)s"),
    ("json-double-quoted", '{"%(k)s":"%(v)s"}'),
    ("python-repr-single-quoted", "{'%(k)s': '%(v)s'}"),
)
_AMP_KEYS = ("password", "token", "client_secret", "api_key")


def test_credential_value_masked_entirely_across_separators():
    """含 `&` / `?` 的凭证值必须整段掩码，不得在分隔符处截断留下明文尾巴。"""
    # 防掩盖前提（沿用 R4/R5/R6 的闸）：两个哨兵单独出现时都原样穿透 —— 证明下面每个
    # pass 都是键名 + 值边界规则的功劳，而不是某条前缀规则代偿。
    for sentinel in (_AMP_HEAD_SENTINEL, _AMP_TAIL_SENTINEL):
        assert http_retry.redact_provider_text(sentinel) == sentinel, (
            f"哨兵 {sentinel} 被某条前缀规则命中 —— 它就无法证明值边界规则是否工作，"
            "请换一个不透明哨兵"
        )

    leaked: list[str] = []
    for key in _AMP_KEYS:
        for value_name, value in _AMP_VALUES:
            for form_name, template in _AMP_FORMS:
                raw = template % {"k": key, "v": value}
                out = http_retry.redact_provider_text(raw)
                where = f"{key}/{value_name}/{form_name}"
                if _AMP_TAIL_SENTINEL in out:
                    leaked.append(f"{where}: 分隔符后留明文尾巴 {raw!r} -> {out!r}")
                elif _AMP_HEAD_SENTINEL in out:
                    leaked.append(f"{where}: 值未被掩码 {raw!r} -> {out!r}")
                elif http_retry.REDACTED_PLACEHOLDER not in out:
                    leaked.append(f"{where}: 值消失但无掩码标记 -> {out!r}")
    assert not leaked, (
        "以下凭证值只掩了一半（值边界把 `&`/`?` 当无条件终止符）:\n" + "\n".join(leaked)
    )

    # SC 冻结的两个具名反例：逐字比对整条输出，尾巴一个字符都不许留。
    assert http_retry.redact_provider_text("password=p@ss&word!") == "password=<redacted>"
    assert http_retry.redact_provider_text("token=abc&def") == "token=<redacted>"


# ---------------------------------------------------------------------------
# SC-R7-02 —— 非回归那一半：query string 里相邻的键仍在 `&` 处分家
# ---------------------------------------------------------------------------
# 修掉明文尾巴的最省事写法是把 `&`/`?` 直接放回值字符类，那会立刻回退成 R5 的形状：
# `a=1&client_secret=X` 里 `a` 的值吃掉 `&` 并连吞后面整个凭证键 → 两个键连成一段掩码，
# 后续键根本轮不到语义判定（相邻的普通键 `b` 也会被吞掉，日志失去可读结构）。
# 本测试与 test_credential_value_masked_entirely_across_separators 是同一判据的正反两面，
# 必须同时绿；实现侧把「断不断开」交给前瞻条件「后面像不像另一个 kv 键」来裁。
_SPLIT_SENTINEL = "opaque-split-sentinel-6b0d92a4"


def test_query_string_keys_still_split_at_ampersand():
    """`&` 后紧跟另一个 kv 键时必须断开，相邻键各自归位、非凭证键不被吞。"""
    # 防掩盖前提：哨兵单独出现时原样穿透。
    assert http_retry.redact_provider_text(_SPLIT_SENTINEL) == _SPLIT_SENTINEL, (
        "哨兵被前缀规则命中 —— 无法证明键名/值边界规则是否工作，请换一个不透明哨兵"
    )

    # 逐字比对整条输出：既验证凭证键被掩，也验证相邻键的值**原样保留**
    #（只断言"哨兵不在输出里"会漏掉"把 a=1 / b=2 一起吞进掩码"这种回归）。
    expectations = (
        (
            f"a=1&client_secret={_SPLIT_SENTINEL}",
            "a=1&client_secret=<redacted>",
        ),
        (
            f"?a=1&refresh_token={_SPLIT_SENTINEL}&b=2",
            "?a=1&refresh_token=<redacted>&b=2",
        ),
        # 两个凭证键相邻：必须各自一个掩码，而不是连成一段
        (
            f"?client_secret={_SPLIT_SENTINEL}&api_key={_SPLIT_SENTINEL}",
            "?client_secret=<redacted>&api_key=<redacted>",
        ),
        # 冒号形态（YAML/JSON 风格的键）同样算"像另一个键"，边界口径一致
        (
            f"a=1&token:{_SPLIT_SENTINEL}&b=2",
            "a=1&token=<redacted>&b=2",
        ),
    )
    drift: list[str] = []
    for raw, expected in expectations:
        out = http_retry.redact_provider_text(raw)
        if out != expected:
            drift.append(f"{raw!r}\n  期望: {expected!r}\n  实得: {out!r}")
    assert not drift, (
        "以下 query string 的键未在 `&` 处正确分家（值把分隔符与后续键一起吞了）:\n"
        + "\n\n".join(drift)
    )
