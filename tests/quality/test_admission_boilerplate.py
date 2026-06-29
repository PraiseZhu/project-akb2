"""准入样板剥离回归测试。

守护 P0 实测发现的 bug：准入三判定此前直接判 raw_content（含 frontmatter +
26 行 §A-§Z 锚点目录 TOC），4000 字窗口被元数据骨架稀释，how-to 实质被挤出
→ gemini 判 "introductory/conceptual" 误拒（含黄金样张 Loop Engineering）。

修复：run_admission 调 _strip_raw_boilerplate 剥掉 frontmatter + 锚点目录，
只把正文喂给三判定。缺这组断言时，删掉剥离逻辑全套仍绿，bug 无声复发。
"""

from __future__ import annotations

from src.akb2.quality.admission import (
    _build_how_tendency_snippet,
    _strip_raw_boilerplate,
    run_admission,
)

# 典型 raw 文件：frontmatter + ## 锚点目录(TOC) + §A_CONTENT_START + how-to 正文
_RAW_WITH_BOILERPLATE = """---
artifact_type: raw
source_slug: addy-osmani
source_title: Loop Engineering
source_authors: Addy Osmani
fetched_via: scrapling_browser
---

## 锚点目录

- §A: CONTENT_START
- §B: INTRODUCTION
- §H: HOW_STEPS
- §S: CODE
- §Z: CONTENT_END


§A_CONTENT_START

### 五大构建块

Loop engineering 的五个可执行步骤：1) 定义 automation 循环目标；
2) 配置 workspace；3) 注册 skills；4) 接 plugins；5) 派 subagents。
配置示例与具体步骤如下……
"""

_LONG_RAW_WITH_LATE_HOW = """---
artifact_type: raw
source_slug: addy-osmani-loop-engineering
source_title: Loop Engineering
fetched_via: defuddle_cli
---

# Addy Osmani "Loop Engineering" 抓取归档

本文件是 wiki/sources 的 raw 归档。引用本归档的论断必须能定位到对应小节。

## 锚点目录

| 锚点 | 内容 |
|------|------|
| §A | 开篇：loop engineering 定义 |
| §B | loop 五要素 + Codex/Claude Code 对照表 |
| §H | 一个完整 loop 的样子 |

---

## §A. 开篇（原文）

""" + ("Loop engineering 是范式变化的概念说明。" * 240) + """

## §B. The five pieces, and then notes

1. Automations that go off on a schedule and do discovery and triage.
2. Worktrees so agents working in parallel do not step on each other.
3. Skills to write down project knowledge.
4. Plugins and connectors to plug the agent into tools.
5. Sub-agents so one agent drafts and another checks it.

配置模板示例：

```toml
[loop]
cadence = "daily"
workflow = "triage -> draft -> review -> promote"
```

## §H. What one loop looks like

可执行步骤：先读取 CI 和 issue，再写入 state file，然后派 maker/checker 两个 agent。
"""


class TestStripRawBoilerplate:
    """_strip_raw_boilerplate 单元行为。"""

    def test_frontmatter_removed(self):
        body = _strip_raw_boilerplate(_RAW_WITH_BOILERPLATE)
        assert "artifact_type:" not in body, "frontmatter 应被剥离"
        assert "fetched_via:" not in body

    def test_anchor_toc_removed(self):
        body = _strip_raw_boilerplate(_RAW_WITH_BOILERPLATE)
        assert "## 锚点目录" not in body, "锚点目录 TOC 应被剥离"
        # TOC 里的 "§B: INTRODUCTION" 列表项不应留下（正文从 §A_CONTENT_START 起）
        assert "- §B: INTRODUCTION" not in body

    def test_body_retained(self):
        body = _strip_raw_boilerplate(_RAW_WITH_BOILERPLATE)
        assert "五大构建块" in body, "正文必须保留"
        assert "可执行步骤" in body

    def test_fail_safe_no_markers_returns_original(self):
        """无 frontmatter/锚点的纯文本 → 回退原文，不判空。"""
        plain = "just some article text without any boilerplate markers"
        assert _strip_raw_boilerplate(plain) == plain

    def test_fail_safe_empty_body_returns_original(self):
        """剥离后为空（仅 frontmatter 无正文）→ 回退原文，不让准入判空。"""
        only_frontmatter = "---\nartifact_type: raw\nsource_slug: x\n---\n"
        out = _strip_raw_boilerplate(only_frontmatter)
        assert out  # 非空
        assert out == only_frontmatter  # 剥到空 → 回退原文


class TestAdmissionSeesBodyNotBoilerplate:
    """run_admission 必须把剥离后的正文喂给 LLM，而非 raw 全文。"""

    def test_llm_receives_stripped_body(self, tmp_path):
        captured: list[str] = []

        def spy_route_llm(model_family, messages, response_schema=None):
            # 捕获送入 LLM 的内容
            for m in messages:
                captured.append(str(m.get("content", "")))

            class _Resp:
                content = '{"pass": true, "reason": "ok"}'

            return _Resp()

        run_admission(
            raw_path=str(tmp_path / "raw.md"),
            raw_content=_RAW_WITH_BOILERPLATE,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            wiki_references=[],
            route_llm_fn=spy_route_llm,
        )

        joined = "\n".join(captured)
        # 喂给 LLM 的内容含正文、不含 frontmatter/TOC 样板
        assert "五大构建块" in joined, "LLM 应看到正文"
        assert "artifact_type:" not in joined, "LLM 不应看到 frontmatter"
        assert "## 锚点目录" not in joined, "LLM 不应看到锚点目录 TOC"


class TestHowTendencyLateContentWindow:
    """How 判定必须能看到后段可执行章节，而不是只看 raw 开头。"""

    def test_how_snippet_prefers_late_how_sections(self):
        stripped = _strip_raw_boilerplate(_LONG_RAW_WITH_LATE_HOW)
        naive_window = stripped[:4000]
        assert "配置模板示例" not in naive_window
        assert "可执行步骤" not in naive_window

        snippet = _build_how_tendency_snippet(stripped)

        assert "## 锚点目录" not in snippet
        assert "配置模板示例" in snippet
        assert "workflow = \"triage -> draft -> review -> promote\"" in snippet
        assert "可执行步骤" in snippet

    def test_run_admission_how_check_receives_late_how_sections(self, tmp_path):
        prompts_by_call: list[str] = []

        def spy_route_llm(model_family, messages, response_schema=None):
            prompts_by_call.append("\n".join(str(m.get("content", "")) for m in messages))

            class _Resp:
                content = '{"pass": true, "reason": "ok"}'

            return _Resp()

        run_admission(
            raw_path=str(tmp_path / "raw.md"),
            raw_content=_LONG_RAW_WITH_LATE_HOW,
            ledger_path=str(tmp_path / "ledger.jsonl"),
            wiki_references=[],
            route_llm_fn=spy_route_llm,
        )

        # SC-1：新流程 sample 非强 procedural → 多一次 lane LLM 调用（topic/how/lane=3）。
        assert len(prompts_by_call) == 3
        how_prompt = prompts_by_call[1]
        assert "配置模板示例" in how_prompt
        assert "workflow = \"triage -> draft -> review -> promote\"" in how_prompt
        assert "可执行步骤" in how_prompt
        assert "## 锚点目录" not in how_prompt
