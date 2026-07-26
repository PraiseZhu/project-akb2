"""DeepSeek V4 Pro batch synthesis — 10 repos → summary + use_case via single LLM call."""

import json
import os
import time
import xml.sax.saxutils as saxutils
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import jsonschema

PROMPT_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "docs"
PROMPT_FILE = "github-deepseek-prompt-v1.md"
ITEM_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "github_synthesis_item_v1.json"

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
        '<output_format>\n[{"repo_url": "...", "summary": "...", '
        '"use_case": "..."}, ...]\n</output_format>'
    )


def _grounding_text(repo: dict[str, Any]) -> str:
    """该 repo 可依据的原文（description + readme）。空串 = 无 grounding。"""
    return "\n".join(
        part for part in (
            (repo.get("description") or "").strip(),
            (repo.get("readme_excerpt") or "").strip(),
        ) if part
    )


def _render_repo_xml(repo: dict[str, Any]) -> str:
    url = saxutils.escape(repo.get("repo_url", ""))
    stars = repo.get("stars", 0)
    language = saxutils.escape(repo.get("language") or "unknown")
    topics = saxutils.escape(", ".join(repo.get("topic_tags", [])))
    mentioned_by = saxutils.escape(", ".join(repo.get("mentioners", [])))
    # description 是 GitHub 侧最短最准的 grounding，必须进 prompt（缺陷 #7）
    description = saxutils.escape((repo.get("description") or "").strip())
    readme = saxutils.escape((repo.get("readme_excerpt") or "")[:500])
    return (
        f'<repo url="{url}">\n'
        f"  stars: {stars}\n"
        f"  language: {language}\n"
        f"  topics: [{topics}]\n"
        f"  mentioned_by: {mentioned_by}\n"
        f"  description: {description}\n"
        f"  readme_excerpt: |\n    {readme}\n"
        f"</repo>"
    )


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~1.3 chars per token for Chinese/English mix."""
    return max(1, len(text) // 2)


def _mock_response(prompt: str) -> dict:
    """显式 mock 产出（只能由 mock=True 触发）。文本自带 MOCK 标记，肉眼可辨。"""
    import re
    input_start = prompt.rfind("<input>")
    search_text = prompt[input_start:] if input_start >= 0 else prompt
    urls = re.findall(r'url="([^"]+)"', search_text)
    mock_data = [
        {"repo_url": url,
         "summary": (
             "[MOCK] 这是链路自检用的占位摘要，非真实 LLM 产出，"
             "不代表该项目的实际功能与技术栈。"
         ),
         "use_case": (
             "[MOCK] 正向场景：仅用于本地链路自检。"
             "反向场景：任何真实阅读场景都不该看到这段文字。"
         )}
        for url in urls
    ]
    return {"choices": [{"message": {"content": json.dumps(mock_data, ensure_ascii=False)}}]}


def _call_deepseek(prompt: str, api_key: str | None = None,
                   *, allow_mock: bool = False) -> dict | None:
    # AI Gateway key：部署侧约定 AI_GATEWAY_API_KEY，
    # 本机运行时设 DEEPSEEK_API_KEY（同一 Gateway key）。
    key = api_key or os.environ.get("AI_GATEWAY_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        # fail-closed（宪法 P3）：旧实现无 key 时返回 mock，而 mock 文本
        # "mock summary text "*5 = 90 字符恰好过 schema minLength:50 → 假简报可入
        # vault。mock 现在必须由调用方显式 opt-in（pipeline --mock），
        # 生产链路缺 key 直接硬失败。
        if allow_mock:
            return _mock_response(prompt)
        raise RuntimeError(
            "synthesis fail-closed: no AI_GATEWAY_API_KEY/DEEPSEEK_API_KEY set. "
            "缺 API key 时不产出任何内容（旧行为会静默产出 mock 假简报）。"
        )

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


def _load_item_schema() -> dict:
    with open(ITEM_SCHEMA_PATH) as f:
        return json.load(f)


def _validate_items(summaries: Any) -> None:
    """逐项 jsonschema 校验（兑现 prompt 文档「少字段或多字段直接 abort」承诺）。"""
    if not isinstance(summaries, list):
        raise ValueError(f"LLM output is not a JSON array: {type(summaries).__name__}")
    validator = jsonschema.Draft7Validator(_load_item_schema())
    for i, item in enumerate(summaries):
        errors = [e.message for e in validator.iter_errors(item)]
        if errors:
            raise ValueError(f"LLM summary item #{i} failed schema: {errors}")


def _align_by_repo_url(repos: list[dict[str, Any]],
                       summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 repo_url 对齐 summary；任何不匹配即 abort。

    旧实现按位置 zip 且 repo_url 不匹配时 `pass`：模型重排数组即整份错位
    （A 仓库配 B 仓库的简介），而数量/schema/URL 全对，无从发现。
    docs/github-deepseek-prompt-v1.md:70 明文承诺 repo_url 不匹配 → abort。
    """
    by_url: dict[str, dict[str, Any]] = {}
    for item in summaries:
        url = item["repo_url"]
        if url in by_url:
            raise ValueError(f"LLM 返回重复 repo_url，拒绝对齐: {url}")
        by_url[url] = item

    expected = [r.get("repo_url", "") for r in repos]
    missing = [u for u in expected if u not in by_url]
    extra = [u for u in by_url if u not in set(expected)]
    if missing or extra:
        raise ValueError(
            f"LLM 返回的 repo_url 与输入不匹配 → abort（防 LLM 编造 repo）；"
            f"missing={missing} unexpected={extra}"
        )

    return [
        {**repo,
         "summary": by_url[repo["repo_url"]]["summary"],
         "use_case": by_url[repo["repo_url"]]["use_case"]}
        for repo in repos
    ]


def synthesize(repos: list[dict[str, Any]], api_key: str | None = None,
               *, mock: bool = False) -> dict[str, Any]:
    """Run batch synthesis for 10 repos. Returns {top_repos: [...], input_tokens: int}.

    Args:
        repos: 已 enrich 的 repo 列表，每项必须有非空 grounding
            （`description` 或 `readme_excerpt`）——空 grounding 直接 abort。
        api_key: gateway key；None 时回落 env，仍无 key 且 mock=False → RuntimeError。
        mock: 显式 opt-in 占位产出（pipeline --mock 自检用）。生产链路禁止。
    """
    ungrounded = [r.get("repo_url", "?") for r in repos if not _grounding_text(r)]
    if ungrounded:
        raise ValueError(
            f"empty grounding → abort（无原文可依据时 LLM 只能凭 repo 名编造）: {ungrounded}"
        )

    prompt = _build_prompt(repos)
    input_tokens = _estimate_tokens(prompt)

    if input_tokens > MAX_INPUT_TOKENS:
        raise ValueError(f"Prompt too large: {input_tokens} tokens > {MAX_INPUT_TOKENS} max")

    response = _call_deepseek(prompt, api_key, allow_mock=mock)
    if response is None:
        raise RuntimeError("DeepSeek API returned no response")

    content = response["choices"][0]["message"]["content"]
    try:
        summaries = json.loads(_extract_json(content))
    except json.JSONDecodeError as e:
        # Retry once with error hint
        retry_prompt = prompt + "\n\n前次输出不是合法 JSON，重新输出严格 JSON 数组。"
        response = _call_deepseek(retry_prompt, api_key, allow_mock=mock)
        if response is None:
            raise RuntimeError("DeepSeek retry also failed") from e
        content = response["choices"][0]["message"]["content"]
        summaries = json.loads(_extract_json(content))

    _validate_items(summaries)

    if len(summaries) != len(repos):
        raise ValueError(f"Summary count mismatch: got {len(summaries)}, expected {len(repos)}")

    return {"top_repos": _align_by_repo_url(repos, summaries), "input_tokens": input_tokens}


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
