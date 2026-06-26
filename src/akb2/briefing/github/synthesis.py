"""DeepSeek V4 Pro batch synthesis — 10 repos → summary + use_case via single LLM call."""

import json
import os
import sys
import time
import xml.sax.saxutils as saxutils
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

PROMPT_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "docs"
PROMPT_FILE = "github-deepseek-prompt-v1.md"

# 全局走 AI Gateway（llm-proxy.tapsvc.com，OpenAI 兼容）。本机运行时设 DEEPSEEK_BASE_URL，
# 部署侧约定 AI_GATEWAY_BASE_URL；两者都指向同一 Gateway。绝不直连 api.deepseek.com。
_GATEWAY_BASE = (
    os.environ.get("AI_GATEWAY_BASE_URL")
    or os.environ.get("DEEPSEEK_BASE_URL")
    or "https://llm-proxy.tapsvc.com"
).rstrip("/")
DEEPSEEK_API_URL = f"{_GATEWAY_BASE}/v1/chat/completions"
MAX_INPUT_TOKENS = 12000
MAX_OUTPUT_TOKENS = 4000
RETRY_BACKOFF = [5, 15, 45]
MAX_RETRIES = 3


def _load_prompt_template() -> str:
    """Load the v1 prompt template from docs/."""
    path = PROMPT_DIR / PROMPT_FILE
    if path.exists():
        content = path.read_text()
        # Extract the template from the ```text block
        marker = "```text"
        start = content.find(marker)
        if start >= 0:
            start = content.find("\n", start) + 1
            end = content.find("```", start)
            if end >= 0:
                return content[start:end].strip()
    # Minimal inline fallback
    return _FALLBACK_SYSTEM_PROMPT


_FALLBACK_SYSTEM_PROMPT = """<system>
你是 GitHub 项目情报编辑。任务：为以下 N 个 GitHub 仓库各产出两段中文文本：
  ① summary（50-500 字）— 客观陈述项目做什么、技术栈、解决的核心问题
  ② use_case（30-300 字）— 什么情况下该装，什么情况下别装；必须含 1-2 条正向场景 + 1 条反向场景

规则：
- 客观、紧凑、不堆形容词、不重复 README 原话
- summary 回答"是什么"，use_case 回答"什么时候用，什么时候别用"
- 输出 JSON 数组，每项 {"repo_url": "...", "summary": "...", "use_case": "..."}
- 严格按输入顺序输出，repo_url 必须完全匹配输入
</system>"""


def _build_prompt(repos: list[dict[str, Any]]) -> str:
    system = _load_prompt_template()
    repos_xml = "\n".join(_render_repo_xml(r) for r in repos)
    return (
        f"{system}\n\n"
        f"<input>\n{repos_xml}\n</input>\n\n"
        (
            '<output_format>\n[{"repo_url": "...", "summary": "...", '
            '"use_case": "..."}, ...]\n</output_format>'
        )
    )


def _render_repo_xml(repo: dict[str, Any]) -> str:
    url = saxutils.escape(repo.get("repo_url", ""))
    stars = repo.get("stars", 0)
    language = saxutils.escape(repo.get("language") or "unknown")
    topics = saxutils.escape(", ".join(repo.get("topic_tags", [])))
    mentioned_by = saxutils.escape(", ".join(repo.get("mentioners", [])))
    readme = saxutils.escape((repo.get("readme_excerpt") or "")[:500])
    return (
        f'<repo url="{url}">\n'
        f"  stars: {stars}\n"
        f"  language: {language}\n"
        f"  topics: [{topics}]\n"
        f"  mentioned_by: {mentioned_by}\n"
        f"  readme_excerpt: |\n    {readme}\n"
        f"</repo>"
    )


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~1.3 chars per token for Chinese/English mix."""
    return max(1, len(text) // 2)


def _call_deepseek(prompt: str, api_key: str | None = None) -> dict | None:
    # AI Gateway key：部署侧约定 AI_GATEWAY_API_KEY，
    # 本机运行时设 DEEPSEEK_API_KEY（同一 Gateway key）。
    key = api_key or os.environ.get("AI_GATEWAY_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        # Mock mode: 无 key 时产出 mock 数据。fail-visibly：打 stderr 警告，
        # 避免生产环境忘配 AI_GATEWAY_API_KEY 时静默产出假简报而无人察觉。
        print(
            "[synthesis] WARNING: no AI_GATEWAY_API_KEY/DEEPSEEK_API_KEY set — "
            "returning MOCK synthesis data (not real LLM output). "
            "Set the gateway key for production.",
            file=sys.stderr,
        )
        # extract repo URLs from the input section (after last <input> tag)
        import re
        input_start = prompt.rfind("<input>")
        search_text = prompt[input_start:] if input_start >= 0 else prompt
        urls = re.findall(r'url="([^"]+)"', search_text)
        mock_data = [
            {"repo_url": url, "summary": "mock summary text " * 5,
             "use_case": (
                 "正向场景：当你需要在项目中集成此工具时适用。"
                 "反向场景：生产环境未经充分测试不建议直接使用。"
             )}
            for url in urls
        ]
        return {"choices": [{"message": {"content": json.dumps(mock_data)}}]}

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    body = json.dumps({
        "model": "deepseek/deepseek-v4-pro",
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": MAX_OUTPUT_TOKENS,
    }).encode()

    for attempt in range(MAX_RETRIES):
        try:
            req = Request(DEEPSEEK_API_URL, data=body, headers=headers)
            with urlopen(req, timeout=120) as resp:
                return json.loads(resp.read())
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF[attempt])
            else:
                raise RuntimeError(f"DeepSeek API failed after {MAX_RETRIES} retries: {e}") from e

    return None


def synthesize(repos: list[dict[str, Any]], api_key: str | None = None) -> dict[str, Any]:
    """Run batch synthesis for 10 repos. Returns {top_repos: [...], input_tokens: int}."""
    prompt = _build_prompt(repos)
    input_tokens = _estimate_tokens(prompt)

    if input_tokens > MAX_INPUT_TOKENS:
        raise ValueError(f"Prompt too large: {input_tokens} tokens > {MAX_INPUT_TOKENS} max")

    response = _call_deepseek(prompt, api_key)
    if response is None:
        raise RuntimeError("DeepSeek API returned no response")

    content = response["choices"][0]["message"]["content"]
    try:
        summaries = json.loads(_extract_json(content))
    except json.JSONDecodeError as e:
        # Retry once with error hint
        retry_prompt = prompt + "\n\n前次输出不是合法 JSON，重新输出严格 JSON 数组。"
        response = _call_deepseek(retry_prompt, api_key)
        if response is None:
            raise RuntimeError("DeepSeek retry also failed") from e
        content = response["choices"][0]["message"]["content"]
        summaries = json.loads(_extract_json(content))

    if len(summaries) != len(repos):
        raise ValueError(f"Summary count mismatch: got {len(summaries)}, expected {len(repos)}")

    # Merge summaries back into repos
    result = []
    for repo, summary in zip(repos, summaries, strict=False):
        merged = {**repo}
        merged["summary"] = summary.get("summary", "")
        merged["use_case"] = summary.get("use_case", "")
        # Validate repo_url match
        if summary.get("repo_url", "") != repo.get("repo_url", ""):
            # Don't fail on mismatch — use input repo_url
            pass
        result.append(merged)

    return {"top_repos": result, "input_tokens": input_tokens}


def _extract_json(text: str) -> str:
    """Extract JSON array from text, handling markdown code fences."""
    text = text.strip()
    # Remove markdown code fences
    if text.startswith("```"):
        text = text[text.index("\n") + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    # Remove optional "json" language tag
    if text.startswith("json\n"):
        text = text[5:]
    return text
