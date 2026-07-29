"""DeepSeek V4 Pro batch synthesis — 10 repos → summary + use_case via single LLM call."""

import json
import os
import time
import xml.sax.saxutils as saxutils
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import jsonschema

from .url_normalizer import normalize

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
RETRY_BACKOFF = [5, 15, 45]
MAX_RETRIES = 3

# ── 输出预算（宪法 P4「预算显式」+ P1「有界自动化」）─────────────────────────────
# 2026-07-21/24/27 三次生产失败（logs/github-briefing-stdout.log:421/457/493）的真因
# 是这个预算不够：旧值 4000 是按「10 × 400 tokens 平均」定的，用平均值当上限，既没有
# worst-case 余量，也没给 deepseek-v4-pro 默认产出的 reasoning_content 留位置。撞顶后
# 半截 JSON 直送 json.loads，被报成 "Unterminated string ..."——预算问题伪装成语法问题。
#
# 重新推导（原则：schema 会**接受**的最长产出都不许被截断，不按平均值定上限）
#   单条字符上限 1000 = summary maxLength 500 + use_case maxLength 300
#                     + repo_url ~120 + JSON 键/引号/逗号/缩进 ~80
#     500/300 取自 schemas/github_briefing_v1.json —— 那是 vault 写入闸的硬上限，
#     闸门放行的长度就必须生成得出来，否则又是「产出合法却被截断」。
#   条数上限 10 —— pipeline `scored[:10]` + schema top_repos maxItems=10 双重硬卡；
#     与候选池无关（candidate_pool_size=61 是博主数，不是条目数），不会随池子增长。
#   信封 ~200 字符 = 数组括号 + 模型偶发的 ```json 围栏（围栏被 _extract_json 剥掉，
#     但生成时照样烧 output token）。
#   token/字符 1.1 —— 三次截断给的实测锚点是「每交付 1 字符烧 1.08~1.61 token」
#     （4000÷3707、4000÷3453、4000÷2490）；比值里偏高的部分是 reasoning 的**固定**
#     开销而非按字符线性增长，故把固定开销单列，字符换算取 1.1。
#   reasoning 余量 2600 —— repair.py:76 已实测 deepseek-v4-pro 即使 reasoning_effort=None
#     也照样产 reasoning_content，且它与 content 共用 max_tokens；2600 ≈ 上述锚点中
#     反推的最大固定开销（4000 − 2490×0.6）。
# → (10 × 1000 + 200) × 1.1 + 2600 = 13,820 → 取 14000（编译期常量，仍然有界）
#
# 注意：这是**上限**不是花费，实际只按生成量计费；真实产出约 390 字符/条（≈4000 tokens
# 总量，见 backstop 成功记录），新上限只在极端长产出时才起作用。
MAX_REPOS_PER_CALL = 10
OUTPUT_CHARS_PER_REPO = 1000
OUTPUT_CHARS_ENVELOPE = 200
OUTPUT_TOKENS_PER_CHAR = 1.1
REASONING_TOKENS_HEADROOM = 2600
MAX_OUTPUT_TOKENS = 14000


class SynthesisTruncatedError(RuntimeError):
    """LLM 输出撞 max_tokens 上限被截断 —— 预算问题，不是 JSON 格式问题。

    单列一个类型而不是复用 JSONDecodeError/ValueError，是为了让「预算撞顶」这条真因
    在日志里自证，并且**绕开** synthesize() 里的 JSON 重试分支：同一请求配同一上限
    重跑必然截在同一处，重试只是白烧一次调用（生产实测每次失败因此翻倍到 103-156s）。
    """


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


def _content_or_raise(response: dict, *, repo_count: int) -> str:
    """取出 message.content；`finish_reason == "length"` 即预算撞顶，按真因抛错。

    OpenAI 兼容格式（AI Gateway / DeepSeek 都是）把停止原因放在
    `choices[0].finish_reason`，`"length"` 就是「被 max_tokens 截断」的官方信号。
    旧实现从不读它（本次修复前全仓 grep finish_reason 零命中），把半截 JSON 直送 json.loads，
    于是「预算不够」在日志里显示成 `Unterminated string starting at: line 50 ...`，
    归因完全错位 —— 违反宪法 P4「预算显式 / 失败可见」。

    `finish_reason` 缺失（None）时不判截断：部分网关不回该字段，不能因为字段缺失
    就把正常产出判成失败。
    """
    choice = response["choices"][0]
    content = choice["message"]["content"]
    if choice.get("finish_reason") != "length":
        return content

    usage = response.get("usage") or {}
    completion_tokens = usage.get("completion_tokens")
    consumed = f"，实际消耗 {completion_tokens} completion tokens" if completion_tokens else ""
    raise SynthesisTruncatedError(
        f"LLM 输出撞 max_tokens 上限被截断（finish_reason=length，"
        f"上限 MAX_OUTPUT_TOKENS={MAX_OUTPUT_TOKENS}{consumed}）——"
        f"这不是 JSON 格式问题，是输出预算不够；本次 {repo_count} 个 repo，"
        f"只拿到 {len(content or '')} 字符的半截产出。"
        f"处置：加大 MAX_OUTPUT_TOKENS 或减少单次 repo 数；不重试同一请求"
        f"（同上限重跑必然截在同一处）。"
    )


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


def _match_key(url: str) -> str:
    """对齐用的比较键 — 两侧同样归一化，把「模型顺手规范化了 URL」与「编造 repo」分开。

    严格全等匹配下，DeepSeek 只要对 URL 做任何无害规范化（补/去尾斜杠、改大小写、
    带 `.git`）就会 missing+extra 同时非空 → 整份简报 abort。归一后仍对不上的才是
    真编造，abort 语义不被削弱。
    """
    if not url:
        return ""
    return normalize(url) or url.strip().rstrip("/").lower()


def _align_by_repo_url(repos: list[dict[str, Any]],
                       summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 repo_url 对齐 summary；任何不匹配即 abort（比较前两侧同样归一化）。

    旧实现按位置 zip 且 repo_url 不匹配时 `pass`：模型重排数组即整份错位
    （A 仓库配 B 仓库的简介），而数量/schema/URL 全对，无从发现。
    docs/github-deepseek-prompt-v1.md:70 明文承诺 repo_url 不匹配 → abort。
    """
    by_url: dict[str, dict[str, Any]] = {}
    for item in summaries:
        key = _match_key(item["repo_url"])
        if key in by_url:
            raise ValueError(f"LLM 返回重复 repo_url，拒绝对齐: {item['repo_url']}")
        by_url[key] = item

    expected = [_match_key(r.get("repo_url", "")) for r in repos]
    missing = [u for u in expected if u not in by_url]
    extra = [u for u in by_url if u not in set(expected)]
    if missing or extra:
        raise ValueError(
            f"LLM 返回的 repo_url 与输入不匹配 → abort（防 LLM 编造 repo）；"
            f"missing={missing} unexpected={extra}"
        )

    return [
        {**repo,
         "summary": by_url[_match_key(repo.get("repo_url", ""))]["summary"],
         "use_case": by_url[_match_key(repo.get("repo_url", ""))]["use_case"]}
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

    # 输出预算是按 MAX_REPOS_PER_CALL 条推导的（见常量区）。条数一旦超过推导前提，
    # 撞顶就从「可能」变成「必然」——在这里响亮地拒掉，而不是等模型截断后在 vault 侧
    # 表现为整份简报消失。pipeline 侧 `scored[:10]` 让这条在生产不可能触发，
    # 它防的是以后有人把 top-N 调大却忘了同步预算。
    if len(repos) > MAX_REPOS_PER_CALL:
        raise ValueError(
            f"单次 synthesize 最多 {MAX_REPOS_PER_CALL} 个 repo，收到 {len(repos)} 个 → 拒绝调用："
            f"输出预算 MAX_OUTPUT_TOKENS={MAX_OUTPUT_TOKENS} 是按 {MAX_REPOS_PER_CALL} 条推导的，"
            f"超出即必然撞顶被截断。要提高条数必须同步重算输出预算。"
        )

    response = _call_deepseek(prompt, api_key, allow_mock=mock)
    if response is None:
        raise RuntimeError("DeepSeek API returned no response")

    # 先判截断再解析：撞顶的半截 JSON 必须报成预算问题，且不进下面的 JSON 重试分支。
    content = _content_or_raise(response, repo_count=len(repos))
    try:
        summaries = json.loads(_extract_json(content))
    except json.JSONDecodeError as e:
        # Retry once with error hint
        retry_prompt = prompt + "\n\n前次输出不是合法 JSON，重新输出严格 JSON 数组。"
        response = _call_deepseek(retry_prompt, api_key, allow_mock=mock)
        if response is None:
            raise RuntimeError("DeepSeek retry also failed") from e
        content = _content_or_raise(response, repo_count=len(repos))
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
