"""LLMAdapter 测试 — DeepSeekAdapter.chat() 调用契约 + JSON 提取容错。

覆盖核心逻辑（非 mock 自证）：
- chat() 把 model/messages/temperature 正确注入 openai client，空响应归一为 ""
- api_key/base_url 的显式值与默认兜底
- openai 包缺失 → RuntimeError 降级提示
- extract_json_from_llm_response 的代码块 / 裸花括号 / 无效三分支

openai client 用 fake 替身记录入参，验证调用契约而非真实 API。
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest import mock

import pytest

from src.akb2.video.llm_adapter import (
    DeepSeekAdapter,
    LLMAdapter,
    extract_json_from_llm_response,
)


# ---------------------------------------------------------------------------
# Fake openai client — 记录 OpenAI(...) 与 chat.completions.create(...) 入参
# ---------------------------------------------------------------------------


def _make_fake_openai(content):
    """返回 (fake_ctor, create_calls, ctor_calls)。

    content 为 create() 返回的 message.content（传 None 验证 `or ""` 分支）。
    """
    create_calls: dict = {}
    ctor_calls: dict = {}

    def fake_create(**kwargs):
        create_calls.update(kwargs)
        message = SimpleNamespace(content=content)
        choice = SimpleNamespace(message=message)
        return SimpleNamespace(choices=[choice])

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=fake_create)
        )
    )

    def fake_ctor(**kwargs):
        ctor_calls.update(kwargs)
        return fake_client

    return fake_ctor, create_calls, ctor_calls


# ---------------------------------------------------------------------------
# Test: chat() 调用契约
# ---------------------------------------------------------------------------


def test_chat_injects_model_messages_temperature():
    """chat() 把 model / system+user messages / temperature 注入 openai create。"""
    adapter = DeepSeekAdapter(
        api_key="sk-test", base_url="https://example.com/v1", model="deepseek-test"
    )
    fake_ctor, create_calls, ctor_calls = _make_fake_openai("hello response")

    # chat() 内是函数级延迟 import（`import openai; openai.OpenAI(...)`），
    # llm_adapter 模块命名空间没有 openai 属性，因此必须 patch openai 模块本身的
    # OpenAI 而非 src.akb2.video.llm_adapter.openai（后者会 AttributeError）。
    with mock.patch("openai.OpenAI", fake_ctor):
        result = adapter.chat(task="classify", system="SYS", user="USR", temperature=0.7)

    assert result == "hello response"
    # 显式 api_key / base_url 透传到 client 构造
    assert ctor_calls["api_key"] == "sk-test"
    assert ctor_calls["base_url"] == "https://example.com/v1"
    # model + temperature 透传到 create
    assert create_calls["model"] == "deepseek-test"
    assert create_calls["temperature"] == 0.7
    # messages 结构 = system + user 两段
    assert create_calls["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USR"},
    ]


def test_chat_empty_content_returns_empty_string():
    """create 返回 content=None → chat 归一为 ""；未给 key/url 时走默认兜底。"""
    adapter = DeepSeekAdapter()  # 无 api_key/base_url
    fake_ctor, _create_calls, ctor_calls = _make_fake_openai(None)

    with mock.patch("openai.OpenAI", fake_ctor):
        result = adapter.chat(task="t", system="s", user="u")

    assert result == ""
    # 默认兜底值
    assert ctor_calls["api_key"] == "sk-placeholder"
    assert ctor_calls["base_url"] == "https://api.deepseek.com"


def test_chat_raises_when_openai_missing():
    """openai 包不可用 → RuntimeError 指明安装方式（依赖缺失降级）。"""
    adapter = DeepSeekAdapter()
    # 将 openai 置为 None → import openai 抛 ImportError
    with mock.patch.dict(sys.modules, {"openai": None}):
        with pytest.raises(RuntimeError, match="openai"):
            adapter.chat(task="t", system="s", user="u")


def test_deepseek_adapter_satisfies_protocol():
    """DeepSeekAdapter 满足 LLMAdapter 运行时协议。"""
    adapter = DeepSeekAdapter()
    assert isinstance(adapter, LLMAdapter)


# ---------------------------------------------------------------------------
# Test: extract_json_from_llm_response 三分支
# ---------------------------------------------------------------------------


def test_extract_json_from_code_block():
    """```json 代码块 → 解析成功。"""
    text = 'prefix\n```json\n{"a": 1, "b": "x"}\n```\nsuffix'
    assert extract_json_from_llm_response(text) == {"a": 1, "b": "x"}


def test_extract_json_from_code_block_without_lang():
    """``` 无 json 标记的代码块也能解析。"""
    text = '```\n{"k": 2}\n```'
    assert extract_json_from_llm_response(text) == {"k": 2}


def test_extract_json_bare_braces():
    """无代码块、裸花括号 → 解析成功。"""
    text = 'noise before {"key": "value"} trailing noise'
    assert extract_json_from_llm_response(text) == {"key": "value"}


def test_extract_json_invalid_returns_none():
    """代码块与裸花括号都是坏 JSON → 两个 except 分支命中后返回 None。"""
    text = "```json\n{not valid json}\n```"
    assert extract_json_from_llm_response(text) is None


def test_extract_json_no_braces_returns_none():
    """完全无花括号 → None。"""
    assert extract_json_from_llm_response("just plain text, no json") is None
