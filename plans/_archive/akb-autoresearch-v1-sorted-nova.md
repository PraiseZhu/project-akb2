# AKB AutoResearch v1 实现计划

## Context

AKB 知识库有 1373 个活跃概念，但其中绝大多数关联度很低（degree < 3）、对应的笔记来源很少。用户目前没有系统方法发现哪些概念值得补研——每次要手动翻图或凭感觉判断。

本计划实现 **边界概念研究建议器**：每次运行 `curate` 或手动调用 `autoresearch` 时，从概念图里找出薄弱但高频的概念，生成可直接复制到飞书/Obsidian 执行的中文 Deep Research query，写入 `_weekly.md` 的新 section。

自动化级别：**建议+一键**。系统只输出 query，不自动创建 NotebookLM 研究、不消耗配额、不回灌报告。

---

## 关键文件

| 文件 | 角色 |
|------|------|
| `scripts/main.py` | 注册 `autoresearch` 子命令 |
| `scripts/commands/autoresearch.py` | CLI handler（新建） |
| `scripts/pipeline/autoresearch.py` | 核心逻辑（新建） |
| `scripts/pipeline/curator.py:1147` | `_generate_weekly_report()` — 插入新 section |
| `config/models.json` | 添加 `research_query` task |
| `tests/test_autoresearch.py` | 单元测试（新建） |

---

## 步骤一：添加 research_query task（`config/models.json`）

在 `task_map` 中追加：

```json
"research_query": {
  "tier": "M",
  "model": "qwen3-8b",
  "no_think": true,
  "timeout": 60
}
```

tier M（qwen3-8b）足够；no_think=true 因为这是结构化生成，不需推理。

---

## 步骤二：新建 `scripts/pipeline/autoresearch.py`

### 数据结构

```python
@dataclass
class BoundaryConcept:
    concept_id: str
    name: str
    type: str
    mention_count: int
    degree: int              # concept_relations 中关联边数
    wiki_source_count: int   # 关联的已合成笔记数
    signals: list[str]       # low_degree / weak_wiki / high_mention_low_link
    score: int
    related_concepts: list[str]   # 相邻活跃概念名，最多5个
    query: str               # 生成的研究 query（初始为空）
```

### BoundaryConceptDetector

```python
class BoundaryConceptDetector:
    def __init__(self, db_path: Path): ...
    def detect(self, limit: int = 10) -> list[BoundaryConcept]: ...
```

**核心 SQL**（一次查询）：

```sql
SELECT
    c.id, c.name, c.type, c.mention_count, c.wiki_page,
    COUNT(DISTINCT cr_id) AS degree,
    COUNT(DISTINCT i.id)  AS wiki_source_count
FROM concepts c
LEFT JOIN (
    SELECT from_id AS cid,
           from_id || ':' || to_id || ':' || relation AS cr_id
    FROM concept_relations
    UNION ALL
    SELECT to_id AS cid,
           from_id || ':' || to_id || ':' || relation AS cr_id
    FROM concept_relations
) cr ON cr.cid = c.id
LEFT JOIN item_concepts ic ON ic.concept_id = c.id
LEFT JOIN items i ON i.id = ic.item_id AND i.note_path IS NOT NULL
WHERE c.status = 'active'
  AND c.canonical_id IS NULL
  AND c.mention_count >= 2
GROUP BY c.id
```

**信号判定**（after query）：

```python
signals = []
if degree < 3:
    signals.append("low_degree")
if wiki_source_count < 5 or wiki_page is None:
    signals.append("weak_wiki")
if mention_count >= 10 and degree < 3:
    signals.append("high_mention_low_link")
```

**排序公式**：

```python
score = (
    (3 if "low_degree" in signals else 0) +
    (2 if "weak_wiki" in signals else 0) +
    (2 if "high_mention_low_link" in signals else 0)
)
# 同分时 mention_count 高者优先
sorted by (-score, -mention_count)
```

**related_concepts** 通过第二次 SQL 获取（每个概念单独查，最多5个）：

```sql
SELECT c2.name FROM concept_relations cr
JOIN concepts c2 ON c2.id = CASE WHEN cr.from_id = ? THEN cr.to_id ELSE cr.from_id END
WHERE (cr.from_id = ? OR cr.to_id = ?) AND c2.status = 'active'
LIMIT 5
```

### ResearchQueryGenerator

```python
class ResearchQueryGenerator:
    def __init__(self, router: LLMRouter): ...
    def generate(self, concepts: list[BoundaryConcept]) -> list[BoundaryConcept]: ...
```

**LLM prompt（每个概念）**：

```python
system = "你是 AI 领域知识研究助手。根据概念名称和类型，生成一句简洁的中文研究查询，用于 Deep Research。直接输出查询语句，不要解释。"
user = f"概念：{c.name}\n类型：{c.type}\n相关概念：{', '.join(c.related_concepts)}"
```

**兜底模板**（LLM 返回 None 或异常时）：

```python
f"{c.name} 的定义、核心原理与最新应用场景"
```

### 公开入口

```python
def run_autoresearch(
    db_path: Path,
    router: LLMRouter,
    limit: int = 10,
) -> list[BoundaryConcept]:
    """检测边界概念并生成 query，供 CLI 和 curator 调用。"""

def generate_weekly_section(concepts: list[BoundaryConcept]) -> str:
    """生成 _weekly.md 的 Markdown section 文本。"""
```

`generate_weekly_section` 格式：

```markdown
## 🎯 边界概念研究建议

> 生成时间：YYYY-MM-DD | 候选 10 个

| # | 概念 | 类型 | 提及 | 关联度 | 信号 | 研究 Query |
|---|------|------|------|--------|------|------------|
| 1 | foo  | entity | 15 | 1 | low_degree, high_mention_low_link | foo 的定义... |
```

---

## 步骤三：新建 `scripts/commands/autoresearch.py`

```python
def cmd_autoresearch(args):
    from scripts.pipeline.autoresearch import run_autoresearch, generate_weekly_section
    from scripts.utils.router import LLMRouter
    
    db_path = Path("index/catalog.db")
    router = LLMRouter()
    concepts = run_autoresearch(db_path, router, limit=args.limit)
    
    if args.output == "json":
        print(json.dumps([asdict(c) for c in concepts], ensure_ascii=False, indent=2))
    else:
        print(generate_weekly_section(concepts))
    
    if not args.dry_run:
        # 写入 _weekly.md
        weekly_path = Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Praise Space/AKB/_weekly.md"
        _splice_autoresearch_section(weekly_path, generate_weekly_section(concepts))
        print(f"[autoresearch] 已写入 {weekly_path}")
```

**`_splice_autoresearch_section(path, section)`**：幂等替换：
- 若文件存在且含 `## 🎯 边界概念研究建议`，找到该 section 的起止行，整段替换
- 若不含该 section，在 `## 推荐采集方向` 行前插入
- 若文件不存在，只打印 warning，不创建

---

## 步骤四：注册到 `scripts/main.py`

在 `sub.add_parser` 区段追加（参照现有 `reddit-digest` 命令格式）：

```python
p_ar = sub.add_parser("autoresearch", help="边界概念研究建议器")
p_ar.add_argument("--suggest", action="store_true", default=True, help="生成研究建议（默认）")
p_ar.add_argument("--limit", type=int, default=10, help="返回候选数量")
p_ar.add_argument("--dry-run", action="store_true", help="只输出，不写入 _weekly.md")
p_ar.add_argument("--output", choices=["text", "json"], default="text")
```

在 `commands` 字典追加：

```python
"autoresearch": lambda args: __import__(
    "scripts.commands.autoresearch", fromlist=["cmd_autoresearch"]
).cmd_autoresearch(args),
```

（参照现有懒加载模式；若 main.py 用 top-level import 则直接 import）

---

## 步骤五：更新 `scripts/pipeline/curator.py:_generate_weekly_report()`

在 `_generate_weekly_report()` 的 lines 构建逻辑中，在生成 `## 推荐采集方向` 的代码**前**插入：

```python
# AutoResearch section（失败不阻塞）
try:
    from scripts.pipeline.autoresearch import run_autoresearch, generate_weekly_section
    ar_concepts = run_autoresearch(self.db_path, self.router, limit=10)
    if ar_concepts:
        lines.append(generate_weekly_section(ar_concepts))
        lines.append("")
except Exception as e:
    logger.warning(f"[autoresearch] 生成失败，跳过: {e}")
```

**注意**：`Curator` 已持有 `self.db_path` 和 `self.router`；直接复用，不新建连接。

---

## 步骤六：新建 `tests/test_autoresearch.py`

### 测试 1：DB 检测逻辑

用 in-memory SQLite fixture（手动建表，不依赖 catalog.db）：

```python
@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE concepts (
            id TEXT PRIMARY KEY, name TEXT, type TEXT,
            mention_count INTEGER DEFAULT 0, wiki_page TEXT,
            status TEXT DEFAULT 'active', canonical_id TEXT
        );
        CREATE TABLE concept_relations (
            from_id TEXT, to_id TEXT, relation TEXT
        );
        CREATE TABLE item_concepts (
            concept_id TEXT, item_id TEXT
        );
        CREATE TABLE items (
            id TEXT PRIMARY KEY, note_path TEXT
        );
    """)
    yield conn
    conn.close()
```

测试用例：
- `test_filters_inactive_concepts`：`status='merged'` 的概念不应出现
- `test_filters_canonical_concepts`：`canonical_id IS NOT NULL` 的概念不应出现
- `test_filters_low_mention`：`mention_count < 2` 的不应出现
- `test_degree_calculation`：插入2条 relations，degree 应为2
- `test_signal_low_degree`：degree=1 → signals 含 `low_degree`
- `test_signal_weak_wiki`：`wiki_source_count < 5` → signals 含 `weak_wiki`
- `test_signal_high_mention_low_link`：`mention_count=10, degree=2` → 含 `high_mention_low_link`
- `test_score_ordering`：高分排前

### 测试 2：Query 生成兜底

```python
def test_fallback_when_llm_unavailable(monkeypatch):
    router = MagicMock()
    router.chat.return_value = None  # LLM 不可用
    gen = ResearchQueryGenerator(router)
    concept = BoundaryConcept(concept_id="c1", name="MCP", ...)
    result = gen.generate([concept])
    assert "MCP" in result[0].query
    assert result[0].query  # 非空

def test_uses_llm_output_when_available(monkeypatch):
    router = MagicMock()
    router.chat.return_value = "MCP 协议的工作原理与主流实现"
    gen = ResearchQueryGenerator(router)
    result = gen.generate([BoundaryConcept(concept_id="c1", name="MCP", ...)])
    assert result[0].query == "MCP 协议的工作原理与主流实现"
```

### 测试 3：Section splice 幂等性

```python
def test_splice_inserts_before_recommend(tmp_path):
    weekly = tmp_path / "_weekly.md"
    weekly.write_text("## 知识覆盖\n\n内容\n\n## 推荐采集方向\n\n推荐内容\n")
    _splice_autoresearch_section(weekly, "## 🎯 边界概念研究建议\n\n表格\n")
    result = weekly.read_text()
    ar_pos = result.index("🎯")
    rec_pos = result.index("推荐采集方向")
    assert ar_pos < rec_pos  # AR section 在推荐前
    assert "推荐内容" in result  # 推荐内容保留

def test_splice_is_idempotent(tmp_path):
    weekly = tmp_path / "_weekly.md"
    weekly.write_text("## 🎯 边界概念研究建议\n\n旧表格\n\n## 推荐采集方向\n\n推荐\n")
    _splice_autoresearch_section(weekly, "## 🎯 边界概念研究建议\n\n新表格\n")
    result = weekly.read_text()
    assert result.count("🎯") == 1  # 不重复
    assert "新表格" in result
    assert "旧表格" not in result
```

---

## 验证步骤

### CLI 验证

```bash
cd "Project AKB"

# 1. Dry-run JSON 输出（不写文件，快速验证检测逻辑）
.venv/bin/python scripts/main.py autoresearch --limit 5 --dry-run --output json

# 2. Dry-run 文本输出（检查 Markdown 格式）
.venv/bin/python scripts/main.py autoresearch --limit 5 --dry-run

# 3. 实际写入（验证 _weekly.md 更新）
.venv/bin/python scripts/main.py autoresearch --limit 10
grep -n "边界概念" "$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Praise Space/AKB/_weekly.md"
```

### 测试验证

```bash
.venv/bin/python -m pytest tests/test_autoresearch.py -v
```

### 回归验证

```bash
.venv/bin/python -m pytest tests/ -q --tb=short   # 全量回归
.venv/bin/python scripts/main.py status           # 管道状态正常
```

### curator 集成验证

```bash
# 只运行 curator（不跑 fetch/translate），观察 _weekly.md 是否含新 section
.venv/bin/python scripts/main.py curate --dry-run 2>&1 | grep -i autoresearch
```

---

## 边界与不做项（v1）

- 不修改 LaunchAgent、不启用任何插件
- 不修复 `main.py search` FTS query 解析 bug（独立 bug）
- 不自动创建 NotebookLM 研究、不回灌报告
- 不触碰 `~/.claude/` 任何配置
- 旧计划 `plans/autoresearch-lively-storm.md` 只作思路参考，不执行其中步骤
- 研究执行由用户手动发起：复制 query → "深度研究 <query>"
