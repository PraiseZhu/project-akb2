"""Scrapling fallback 不回归测试 — SC-5。

三类直测（全程 mock 无网络）:
  (a) adaptive 成功 → 走 Fetcher.get（非已废弃的实例 .fetch）
  (b) browser 路径 → 走 StealthyFetcher.fetch（classmethod）
  (c) 抛错 → _extract_via_scrapling 返回 {} → extract_content 不崩 → fail-closed
      （method==FAILED，给 failures_path 时落签名）

断言绑可观测行为（方法名 / 返回值），不焊死调用次数。
"""

from __future__ import annotations

import sys
import types

import pytest

from akb2.quality.intake.extract import (
    ExtractMethod,
    _extract_via_scrapling,
    extract_content,
)

# ---------------------------------------------------------------------------
# Fakes — 模拟 scrapling 0.4.8 的 classmethod API
# ---------------------------------------------------------------------------


class _FakePage:
    """模拟 scrapling Response：含 html_content / body / title。"""

    def __init__(
        self,
        html: str = "<html><body>rendered content long enough to pass</body></html>",
    ) -> None:
        self.html_content = html
        self.body = html.encode("utf-8")
        self.title = "Stub Title"


class _FakeFetcher:
    """模拟 scrapling.Fetcher（classmethod get，无实例化）。"""

    calls: list[tuple[str, str]] = []
    raise_exc: BaseException | None = None

    @classmethod
    def get(cls, url: str, **kwargs: object) -> _FakePage:
        cls.calls.append(("Fetcher.get", url))
        if cls.raise_exc is not None:
            raise cls.raise_exc
        return _FakePage()

    # 故意不提供 .fetch —— 与 scrapling 0.4.8 一致，若改回实例 .fetch 会触发 AttributeError


class _FakeStealthyFetcher:
    """模拟 scrapling.StealthyFetcher（classmethod fetch，无实例化）。"""

    calls: list[tuple[str, str]] = []
    raise_exc: BaseException | None = None

    @classmethod
    def fetch(cls, url: str, **kwargs: object) -> _FakePage:
        cls.calls.append(("StealthyFetcher.fetch", url))
        if cls.raise_exc is not None:
            raise cls.raise_exc
        return _FakePage()


# ---------------------------------------------------------------------------
# Fixture — 注入 fake scrapling + 绕过 import gate + 禁真实 defuddle subprocess
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stub_scrapling(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = types.ModuleType("scrapling")
    mod.Fetcher = _FakeFetcher  # type: ignore[attr-defined]
    mod.StealthyFetcher = _FakeStealthyFetcher  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "scrapling", mod)

    # 绕过 _check_tool_available 的 find_spec 门（测试环境全 mock，不发网络）
    monkeypatch.setattr(
        "akb2.quality.intake._check_tool_available",
        lambda name, probe=None, logger=None: True,
    )
    # 禁真实 defuddle subprocess：渲染 HTML 交给 defuddle 解析时返回空（走 scrapling 兜底）
    monkeypatch.setattr(
        "akb2.quality.intake.extract._extract_via_defuddle",
        lambda url, html=None: {},
    )

    # 重置调用追踪器
    _FakeFetcher.calls = []
    _FakeFetcher.raise_exc = None
    _FakeStealthyFetcher.calls = []
    _FakeStealthyFetcher.raise_exc = None


# ---------------------------------------------------------------------------
# (a) adaptive 成功 → 走 Fetcher.get
# ---------------------------------------------------------------------------


def test_adaptive_uses_fetcher_get() -> None:
    url = "https://example.com/article"
    result = _extract_via_scrapling(url, html=None, browser_mode=False)

    # 断言走的是 classmethod Fetcher.get（非已废弃的实例 .fetch）
    assert any(c[0] == "Fetcher.get" and c[1] == url for c in _FakeFetcher.calls)
    # browser 分支不应被触发
    assert not _FakeStealthyFetcher.calls
    # 返回非空 dict（fallback 路径用 rendered_html 作 content）
    assert result and result.get("content")


# ---------------------------------------------------------------------------
# (b) browser 路径 → 走 StealthyFetcher.fetch
# ---------------------------------------------------------------------------


def test_browser_uses_stealthy_fetch() -> None:
    url = "https://example.com/anti-bot"
    result = _extract_via_scrapling(url, html=None, browser_mode=True)

    assert any(
        c[0] == "StealthyFetcher.fetch" and c[1] == url for c in _FakeStealthyFetcher.calls
    )
    # adaptive 分支不应被触发
    assert not _FakeFetcher.calls
    assert result and result.get("content")


# ---------------------------------------------------------------------------
# (c) 抛错 → 返回 {} → extract_content 不崩 → fail-closed
# ---------------------------------------------------------------------------


def test_scrapling_raises_returns_empty_dict() -> None:
    """Fetcher.get 抛错时 _extract_via_scrapling fail-closed 返回 {}。"""
    _FakeFetcher.raise_exc = RuntimeError("'Fetcher' object has no attribute 'fetch'")
    url = "https://example.com/broken"

    result = _extract_via_scrapling(url, html=None, browser_mode=False)

    assert result == {}, "scrapling 抛错应返回空 dict（fail-closed）"
    # Fetcher.get 确实被调用过（只是抛了异常）
    assert any(c[0] == "Fetcher.get" for c in _FakeFetcher.calls)


def test_extract_content_fail_closed_on_scrapling_error(
    tmp_path: pytest.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """extract_content 在 scrapling 抛错时不崩：method==FAILED，给 failures_path 时落签名。"""
    _FakeFetcher.raise_exc = RuntimeError("scrapling boom")

    # 拦截 _record_fetch_fail，避免依赖真实 signatures 模块
    sig_written: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "akb2.quality.intake.extract._record_fetch_fail",
        lambda failures_path, url, error: sig_written.append(
            (failures_path, url, error)
        ),
    )

    url = "https://example.com/fail"
    failures_path = str(tmp_path / "signatures.jsonl")
    # 提供短 html（非 None → 避免真实 httpx 抓取；非反爬 → 先走 defuddle 再走 scrapling）
    result = extract_content(
        url=url,
        html="<html><body>short</body></html>",
        failures_path=failures_path,
    )

    # fail-closed：不产 raw，method==FAILED
    assert result.method == ExtractMethod.FAILED
    assert not result.extraction_success
    # 签名落账
    assert len(sig_written) == 1
    assert sig_written[0][0] == failures_path
    assert sig_written[0][1] == url
