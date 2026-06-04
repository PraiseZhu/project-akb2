"""LLM 适配器 — 视频管线 L5 话题聚类去重 + 降级摘要接口。

将 LLM 调用抽象为可注入接口，L5 话题去重测试可 mock adapter 验证调用而非真实 LLM。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Protocol, runtime_checkable

logger = logging.getLogger("akb2.video.llm_adapter")


@runtime_checkable
class LLMAdapter(Protocol):
    """LLM 调用接口 — 可被 mock/真实实现替换。"""

    def chat(
        self,
        task: str,
        system: str,
        user: str,
        temperature: float = 0.3,
    ) -> str:
        """发送 chat 请求，返回文本响应。"""
        ...


class DeepSeekAdapter:
    """DeepSeek API 适配器（AKB2 当前 LLM 后端）。"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "deepseek-chat",
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._model = model

    def chat(
        self,
        task: str,
        system: str,
        user: str,
        temperature: float = 0.3,
    ) -> str:
        """调 DeepSeek API 做 chat completion。"""
        try:
            import openai  # type: ignore[import-untyped]
        except ImportError:
            raise RuntimeError(
                "openai 包未安装。安装: pip install openai"
            )

        client = openai.OpenAI(
            api_key=self._api_key or "sk-placeholder",
            base_url=self._base_url or "https://api.deepseek.com",
        )
        response = client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
        )
        return response.choices[0].message.content or ""


def extract_json_from_llm_response(text: str) -> dict | None:
    """从 LLM 响应中提取 JSON 对象（带代码块/裸 JSON 容错）。"""
    # 优先匹配 ```json ... ``` 代码块
    code_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if code_match:
        try:
            return json.loads(code_match.group(1))
        except json.JSONDecodeError:
            pass
    # 尝试匹配最外层花括号
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass
    return None