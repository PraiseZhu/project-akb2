"""渲染飞书 interactive 卡片 — 全文铺开（用户选）。

- AI 趋势：正文 markdown 全文，markdown_to_lark_md + split_for_card 拆多个 markdown 元素
- GitHub：优先用 .json 的 top_repos 结构化渲染，缺 json 时回退正文 md
"""

from __future__ import annotations

from .feishu.lark_md import markdown_to_lark_md, split_for_card
from .state import Briefing

_HEADER_TEMPLATE = {"ai_trends": "blue", "github": "turquoise"}


def _markdown_elements(text: str) -> list[dict]:
    lark = markdown_to_lark_md(text)
    return [{"tag": "markdown", "content": chunk} for chunk in split_for_card(lark)]


def _github_markdown(json_data: dict) -> str:
    repos = json_data.get("top_repos", [])
    lines: list[str] = []
    for i, r in enumerate(repos, 1):
        url = r.get("repo_url", "")
        name = url.split("github.com/", 1)[-1].rstrip("/") if "github.com/" in url else (url or "?")
        stars = r.get("stars", "?")
        lang = r.get("language") or ""
        suffix = f" · {lang}" if lang else ""
        lines.append(f"**{i}. [{name}]({url})** ⭐{stars}{suffix}")
        if r.get("summary"):
            lines.append(f"做什么：{r['summary']}")
        if r.get("use_case"):
            lines.append(f"适用：{r['use_case']}")
        lines.append("")
    return "\n".join(lines).strip()


def _build_card(title: str, kind: str, body_text: str) -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": _HEADER_TEMPLATE.get(kind, "blue"),
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": _markdown_elements(body_text),
    }


def render_cards(b: Briefing) -> list[dict]:
    """返回该简报对应的飞书卡片列表（当前为单卡，正文拆多元素）。"""
    if b.kind == "github" and b.json_data:
        body_text = _github_markdown(b.json_data)
    else:
        body_text = b.body
    if not body_text.strip():
        body_text = "_（本期简报正文为空）_"
    return [_build_card(b.title, b.kind, body_text)]
