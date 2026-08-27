"""akb2.retrieval 引擎 + CLI 契约测试。

正例：fixture vault 中 promoted 笔记可检出且 how_density/how_executability 非空。
负例（exact 集合判定，hardening 6）：staging 候选 / 隔离区 / wiki/sources 内
未升格笔记 / 索引页 / reviewer 非主链路签名 —— 即使内容与 promoted 笔记相同
也不得出现在结果中。

vault 全部用临时 fixture，不依赖本机 /Volumes/AKB2。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from akb2.retrieval import engine

# ---------------------------------------------------------------------------
# fixture vault 构造
# ---------------------------------------------------------------------------

_PROMOTED_FM = """---
title: Loop Engineering 黄金样张
page_status: active
status: active
slug: addy-osmani-loop-engineering
gate_decision: active
reviewer: akb2-quality-loop-v1
reviewed_at: 2026-06-11
related_concepts: ["[[concept-harness-engineering]]", "[[concept-agent-collaboration]]"]
source_refs:
  - raw/addy-osmani-loop-engineering--raw--2026-06-11.md
---
"""

_HOW_BODY = """## TLDR

Loop engineering 是把任务做成有目标、验证与停止条件的受控闭环。

## 论证与解释

正文论证段落，含 loop 与 engineering 关键词。

### 操作步骤

1. [raw §C] (source_grounded) 定义验证目标并跑全量测试 `pytest -q tests/`
2. [raw §D] (akb_interpretation) AKB action: 固化变异执行器; source_basis=[raw §D]; unsupported_assumptions=命令对象名与生产 API 需落地校准 `python3 scripts/mutation_harness.py --mutation demo`
3. [raw §E] (source_grounded) 对账清单逐条核对 `bash scripts/check_ledger.sh`

## 适用边界

适用于可机器验证的工程 loop。见 [[concept-context-engineering]] 与 [[loop-engineering]]。
"""

_STAGING_FM = """---
title: Loop Engineering 黄金样张
page_status: candidate
status: candidate
slug: addy-osmani-loop-engineering
gate_decision: null
proposed_gate_decision: active
---
"""


def _make_vault(tmp_path: Path) -> Path:
    """构造含正例 + 全套负例的 fixture vault，返回 vault 根。"""
    root = tmp_path / "vault"
    sources = root / "wiki" / "sources"
    sources.mkdir(parents=True)

    # 正例：promoted 笔记（promote._flip_frontmatter 同口径四要素）
    (sources / "addy-osmani-loop-engineering.md").write_text(
        _PROMOTED_FM + _HOW_BODY, encoding="utf-8"
    )

    # 第二篇 promoted（不同主题，供 AND/limit 测试）
    (sources / "mcp-readonly-server.md").write_text(
        """---
title: MCP 只读 Server 模式
page_status: active
status: active
slug: mcp-readonly-server
gate_decision: active
reviewer: akb2-quality-loop-v1
reviewed_at: 2026-07-01
---

## TLDR

只读 mcp server 是零写零网络契约的检索接口（retrieval engineering 实践）。

## 论证与解释

server 侧 router=None 降级为关键词检索。

### 操作步骤

1. [raw §A] (source_grounded) 冒烟验证 `python3 -m akb2.retrieval.cli search mcp`
""",
        encoding="utf-8",
    )

    # 负例 1：staging 候选 —— 同名同正文，status: candidate
    staging_candidate = (
        root / "staging" / "candidate" / "manual-source-intake-2026-06-11"
    )
    staging_candidate.mkdir(parents=True)
    (staging_candidate / "addy-osmani-loop-engineering.md").write_text(
        _STAGING_FM + _HOW_BODY, encoding="utf-8"
    )

    # 负例 2：隔离区 —— 即使带全套 promoted 标记也不得进入结果
    quarantine = root / "staging" / "quarantine"
    quarantine.mkdir(parents=True)
    (quarantine / "addy-osmani-loop-engineering.md").write_text(
        _PROMOTED_FM + _HOW_BODY, encoding="utf-8"
    )

    # 负例 3：wiki/sources 内未升格笔记（status: candidate）
    (sources / "not-promoted-yet.md").write_text(
        """---
title: Loop Engineering 未升格稿
page_status: candidate
status: candidate
gate_decision: null
---

## 论证与解释

### 操作步骤

1. 草稿内容 loop engineering `echo draft`
""",
        encoding="utf-8",
    )

    # 负例 4：active 但 reviewer 非主链路签名
    (sources / "human-reviewed.md").write_text(
        """---
title: Loop Engineering 人工审稿
page_status: active
status: active
gate_decision: active
reviewer: praise
---

## 论证与解释

### 操作步骤

1. 人工内容 loop engineering `echo human`
""",
        encoding="utf-8",
    )

    # 负例 5：索引页（收尾段维护，非五闸升格产出）
    (sources / "00-Index.md").write_text(
        """---
title: 索引
page_status: active
status: active
gate_decision: active
reviewer: akb2-quality-loop-v1
---

## §4 blog / 2026-06

- Loop Engineering 黄金样张：loop engineering 入库摘要
""",
        encoding="utf-8",
    )
    return root


# ---------------------------------------------------------------------------
# 正例
# ---------------------------------------------------------------------------


def test_promoted_note_found_with_how_fields(tmp_path):
    root = _make_vault(tmp_path)
    results = engine.search(root, "loop engineering")
    assert [r.path for r in results] == [
        "wiki/sources/addy-osmani-loop-engineering.md"
    ]
    hit = results[0]
    assert hit.title == "Loop Engineering 黄金样张"
    # How 密度内容：How 节全文非空且含步骤
    assert "操作步骤" not in hit.how_density  # 节标题之后的内容
    assert "定义验证目标" in hit.how_density
    assert "pytest -q tests/" in hit.how_density
    # How 可执行性内容：反引号命令清单
    assert hit.how_executability == [
        "pytest -q tests/",
        "python3 scripts/mutation_harness.py --mutation demo",
        "bash scripts/check_ledger.sh",
    ]


def test_old_style_how_template_heading_extracted(tmp_path):
    """历史 golden 样张形态：## xxx(How 模板) 节可提取。"""
    root = tmp_path / "vault"
    sources = root / "wiki" / "sources"
    sources.mkdir(parents=True)
    (sources / "legacy-loop.md").write_text(
        """---
title: Legacy Loop Note
page_status: active
status: active
gate_decision: active
reviewer: akb2-quality-loop-v1
---

## 一个完整 loop 的形状(How 模板)

1. 建立目标 `echo goal`
2. 验证收敛 `echo verify`
""",
        encoding="utf-8",
    )
    results = engine.search(root, "loop")
    assert len(results) == 1
    assert "建立目标" in results[0].how_density
    assert results[0].how_executability == ["echo goal", "echo verify"]


def test_how_section_not_truncated_by_heading_inside_fence(tmp_path):
    """围栏代码块内以 # 开头的行（如脚本注释）不得被误判为下一节标题。"""
    root = tmp_path / "vault"
    sources = root / "wiki" / "sources"
    sources.mkdir(parents=True)
    body = """## 论证与解释

正文论证段落，含 loop engineering 关键词。

### 操作步骤

1. [raw §C] (source_grounded) 执行迁移脚本：

```bash
# backup first
cp db.sqlite db.sqlite.bak
```

2. [raw §D] (source_grounded) 验证结果 `pytest -q tests/`

## 适用边界

适用范围说明。
"""
    (sources / "fenced-heading.md").write_text(_PROMOTED_FM + body, encoding="utf-8")
    results = engine.search(root, "loop engineering")
    assert len(results) == 1
    how = results[0].how_density
    assert "验证结果" in how
    assert "pytest -q tests/" in how
    assert results[0].how_executability == ["pytest -q tests/"]


def test_how_start_heading_inside_fence_ignored(tmp_path):
    """围栏里的伪 `### 操作步骤` 不得当 How 起点（起始扫描须 fence-aware）。

    反例：论证段先出现一个围栏示例块，内部含 `### 操作步骤` + 伪造列表。
    起始扫描若不认 fence，截出的正文会以「- 伪造示例」开头、真步骤被拼
    在残留内容之后。
    """
    root = tmp_path / "vault"
    sources = root / "wiki" / "sources"
    sources.mkdir(parents=True)
    body = """## 论证与解释

正文论证段落，含 loop engineering 关键词。示例结构（非文档结构）：

```markdown
### 操作步骤
- 伪造示例 `evil.sh`
```

### 操作步骤

1. [raw §C] (source_grounded) 真步骤一：执行迁移 `python3 -m migrate`

2. [raw §D] (source_grounded) 真步骤二：验证 `pytest -q tests/`

## 适用边界

适用范围说明。
"""
    (sources / "fenced-how-start.md").write_text(_PROMOTED_FM + body, encoding="utf-8")
    results = engine.search(root, "loop engineering")
    assert len(results) == 1
    how = results[0].how_density
    assert how.startswith("1. [raw §C]"), f"How 起点错位: {how[:60]!r}"
    assert "伪造示例" not in how
    assert "evil.sh" not in results[0].how_executability
    assert results[0].how_executability == [
        "python3 -m migrate",
        "pytest -q tests/",
    ]


def test_extract_how_section_linear_on_large_headingless_note():
    """性能回归：无 How 标题的大笔记必须线性耗时，不得逐行回扫前缀。

    反例（O(n²) 起始扫描）：10k 行无标题笔记在修前耗 cpu ≈3.2s，二次
    曲线（n 翻倍 ≈4×）；线性实现应为毫秒级。上限给 0.25s 松阈防 flake，
    同时用 n=5k vs 10k 的耗时比 <=4 排除「快了但仍二次」的假阴性。
    """
    import time

    def _probe(n: int) -> float:
        body = "\n".join(f"line {i} some text without heading" for i in range(n))
        t0 = time.process_time()
        result = engine.extract_how_section(body)
        elapsed = time.process_time() - t0
        assert result == "", "无 How 标题的笔记必须返回空串"
        return elapsed

    t5k = _probe(5_000)
    t10k = _probe(10_000)
    assert t10k < 0.25, f"10k 行耗时 {t10k:.3f}s 超上限（疑似回到二次扫描）"
    assert t10k <= max(t5k * 4, 0.05), (
        f"耗时随 n 二次增长：5k={t5k:.4f}s 10k={t10k:.4f}s"
    )


def test_inline_backticks_inside_fence_not_extracted_as_commands(tmp_path):
    """围栏代码块内部的反引号跨度（示例文本）不得混入可执行命令清单。"""
    root = tmp_path / "vault"
    sources = root / "wiki" / "sources"
    sources.mkdir(parents=True)
    body = """## 论证与解释

正文论证段落，含 loop engineering 关键词。

### 操作步骤

1. [raw §C] (source_grounded) 参考示例：

```markdown
使用 `foo.bar()` 调用即可，不是真实命令
```

2. [raw §D] (source_grounded) 真实命令 `pytest -q tests/`

## 适用边界

适用范围说明。
"""
    (sources / "fenced-backtick.md").write_text(_PROMOTED_FM + body, encoding="utf-8")
    results = engine.search(root, "loop engineering")
    assert len(results) == 1
    assert results[0].how_executability == ["pytest -q tests/"]


def test_and_semantics_and_limit(tmp_path):
    root = _make_vault(tmp_path)
    # AND：两 token 必须同时命中，只有黄金样张满足
    assert [r.path for r in engine.search(root, "loop engineering")] == [
        "wiki/sources/addy-osmani-loop-engineering.md"
    ]
    # 单 token 命中两篇 promoted，limit 截断到 1
    both = engine.search(root, "engineering")
    assert {r.path for r in both} == {
        "wiki/sources/addy-osmani-loop-engineering.md",
        "wiki/sources/mcp-readonly-server.md",
    }
    assert len(engine.search(root, "engineering", limit=1)) == 1
    # 缺省 / limit<=0：不截断，返回全部命中
    assert len(engine.search(root, "engineering")) == 2
    assert len(engine.search(root, "engineering", limit=0)) == 2


def test_graph_fields_from_related_concepts_and_wikilinks(tmp_path):
    root = _make_vault(tmp_path)
    hit = engine.search(root, "loop engineering")[0]
    assert hit.related_concepts == [
        "concept-harness-engineering",
        "concept-agent-collaboration",
    ]
    assert "concept-context-engineering" in hit.graph_links
    assert "loop-engineering" in hit.graph_links
    assert hit.graph_links == list(dict.fromkeys(hit.graph_links))


def test_related_concepts_yaml_block_list_and_wikilink_alias(tmp_path):
    """生成器常用 YAML block list；alias 不得拆成 Loop / Engineering。"""
    root = tmp_path / "vault"
    sources = root / "wiki" / "sources"
    sources.mkdir(parents=True)
    sources.joinpath("graph-block.md").write_text(
        """---
title: Graph block
page_status: active
status: active
gate_decision: active
reviewer: akb2-quality-loop-v1
related_concepts:
  - [[concept-harness-engineering]]
  - [[loop_engineering|Loop Engineering]]
---

graph 正文见 [[quality-gate]]。
""",
        encoding="utf-8",
    )
    sources.joinpath("graph-alias-flow.md").write_text(
        """---
title: Graph alias flow
page_status: active
status: active
gate_decision: active
reviewer: akb2-quality-loop-v1
related_concepts: ["[[loop_engineering|Loop Engineering]]", "[[quality-gate]]"]
---

graph 正文。
""",
        encoding="utf-8",
    )
    block = engine.search(root, "graph block")[0]
    assert block.related_concepts == [
        "concept-harness-engineering",
        "concept-loop-engineering",
    ]
    assert "Loop" not in block.related_concepts
    assert "Engineering" not in block.related_concepts
    assert "quality-gate" in block.graph_links
    assert "Loop" not in block.graph_links
    assert "Engineering" not in block.graph_links
    flow = engine.search(root, "graph alias")[0]
    assert flow.related_concepts == ["concept-loop-engineering", "quality-gate"]
    assert "Loop" not in flow.related_concepts
    assert "Engineering" not in flow.related_concepts


def test_no_match_and_missing_sources_dir(tmp_path):
    root = _make_vault(tmp_path)
    assert engine.search(root, "不存在的关键词xyz") == []
    # wiki/sources 不存在 → 空结果不崩溃
    assert engine.search(tmp_path / "empty-vault", "loop") == []


# ---------------------------------------------------------------------------
# 负例：样本集 exact（hardening 6）
# ---------------------------------------------------------------------------


def test_staging_candidate_never_in_results(tmp_path):
    """staging 同名同正文候选不得进入结果——exact 集合判定。"""
    root = _make_vault(tmp_path)
    results = engine.search(root, "loop engineering")
    paths = [r.path for r in results]
    assert paths == ["wiki/sources/addy-osmani-loop-engineering.md"]
    assert all(not p.startswith("staging/") for p in paths)


def test_quarantine_never_in_results(tmp_path):
    """隔离区内容即使带全套 promoted 标记也不得进入结果。"""
    root = _make_vault(tmp_path)
    for r in engine.search(root, "loop engineering"):
        assert "quarantine" not in r.path


def test_unpromoted_and_foreign_reviewer_excluded(tmp_path):
    """wiki/sources 内 status: candidate 与 reviewer 非主链路签名均排除。"""
    root = _make_vault(tmp_path)
    paths = {r.path for r in engine.search(root, "engineering")}
    assert "wiki/sources/not-promoted-yet.md" not in paths
    assert "wiki/sources/human-reviewed.md" not in paths


def test_index_file_excluded(tmp_path):
    root = _make_vault(tmp_path)
    paths = {r.path for r in engine.search(root, "loop engineering")}
    assert "wiki/sources/00-Index.md" not in paths


def test_proposed_gate_decision_prefix_not_matched(tmp_path):
    """行首锚定：staging 的 proposed_gate_decision: active 不构成升格标记。"""
    fm = "---\nstatus: candidate\nproposed_gate_decision: active\n---\n"
    assert engine.is_promoted(fm) is False
    fm_full = (
        "---\npage_status: active\nstatus: active\n"
        "gate_decision: active\nreviewer: akb2-quality-loop-v1\n---\n"
    )
    assert engine.is_promoted(fm_full) is True


# ---------------------------------------------------------------------------
# CLI 契约（python3 -m akb2.retrieval.cli search ... --output json）
# ---------------------------------------------------------------------------


def _run_cli(repo_root: Path, args: list[str], env_extra: dict[str, str]) -> str:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src")
    env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, "-m", "akb2.retrieval.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(repo_root),
    )
    assert proc.returncode == 0, f"CLI 失败: {proc.stderr}"
    return proc.stdout


def test_cli_search_json_contract(repo_root, tmp_path):
    root = _make_vault(tmp_path)
    out = _run_cli(
        repo_root,
        ["search", "loop engineering", "--limit", "5", "--output", "json",
         "--vault-root", str(root)],
        {},
    )
    payload = json.loads(out)
    assert payload["query"] == "loop engineering"
    assert payload["limit"] == 5
    assert payload["count"] == 1
    (hit,) = payload["results"]
    assert set(hit) == {
        "path",
        "title",
        "how_density",
        "how_executability",
        "score",
        "related_concepts",
        "graph_links",
    }
    assert "concept-harness-engineering" in hit["related_concepts"]
    assert "concept-context-engineering" in hit["graph_links"]
    assert hit["path"] == "wiki/sources/addy-osmani-loop-engineering.md"
    assert hit["title"] == "Loop Engineering 黄金样张"
    assert "pytest -q tests/" in hit["how_density"]
    assert "pytest -q tests/" in hit["how_executability"]


def test_cli_vault_root_via_env(repo_root, tmp_path):
    """不传 --vault-root 时经 VAULT_ROOT env 解析（loops.paths 契约）。"""
    root = _make_vault(tmp_path)
    akb2_root = tmp_path / "Obsidian" / "Praise Space" / "AKB2.0"
    akb2_root.parent.mkdir(parents=True, exist_ok=True)
    akb2_root.symlink_to(root.resolve(), target_is_directory=True)
    out = _run_cli(
        repo_root,
        ["search", "mcp", "--limit", "3"],
        {"VAULT_ROOT": str(tmp_path / "Obsidian")},
    )
    payload = json.loads(out)
    assert payload["count"] == 1
    assert payload["results"][0]["path"] == "wiki/sources/mcp-readonly-server.md"
    akb2_root.unlink()


def test_cli_no_results_still_valid_json(repo_root, tmp_path):
    root = _make_vault(tmp_path)
    out = _run_cli(
        repo_root,
        ["search", "不存在关键词xyz", "--limit", "10", "--output", "json",
         "--vault-root", str(root)],
        {},
    )
    payload = json.loads(out)
    assert payload["count"] == 0
    assert payload["results"] == []
