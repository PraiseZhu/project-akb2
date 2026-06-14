# Step 2: 抽取三工具接真

GD_STANDARD: Project GD/prompts/gd-review-standard.md
TEMPLATE_KIND: gd-step-plan

日期: 2026-06-14 | 状态: draft

---

## 1. 目标链

```text
TASK_GOAL: _extract_via_defuddle/_via_scrapling/_via_docling 三方法从占位 stub 改为真调用；HTTP fetcher 入口；MIN_CONTENT_LENGTH 上调；word_count 基于 markdown 正文；import gate 探测。pyproject 依赖修改归 step-6 所有。
```

REVIEW_DOMAIN: ai_infra
REVIEW_FOCUS: `三工具真实 API 调用(F4); HTTP fetcher 入口; import gate 探测(P3); word_count 计数方式`

---

## 2. 前置条件

- blocked_by: step-1（ExtractionContext 已定义）
- Hard-stop: 改动导致 test_intake_extract.py 任一用例红 → 修复后再进 step-3

---

## 3. 成功标准（SC）

- [ ] SC-2（defuddle 接真）: `_extract_via_defuddle` 调 `defuddle parse <tmpfile> --json -m`；word_count 基于 markdown
  - verify (method: test): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python -m pytest tests/quality/test_intake_extract.py::TestExtractViaDefuddle -q`
  - expect: exit 0; "content 字段无 HTML 标签；title 非写死字符串 'Defuddle Extracted Title'"

- [ ] SC-3（scrapling 接真）: `_extract_via_scrapling` 调 `scrapling.Fetcher`/`StealthyFetcher`；browser_mode=True 走 playwright
  - verify (method: test): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python -m pytest tests/quality/test_intake_extract.py::TestExtractViaScrapling -q`
  - expect: exit 0; "自适应和浏览器模式各覆盖 1 个 fixture；title 非 'Scrapling adaptive Title'"

- [ ] SC-4（docling 接真）: `_extract_via_docling` 调 `DocumentConverter().convert()`；返回 export_to_markdown()
  - verify (method: test): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python -m pytest tests/quality/test_intake_extract.py::TestExtractViaDocling -q`
  - expect: exit 0; "content 非 bytes.decode() 原始 PDF 文本"

---

## 6. 实现步骤

```text
Step.1
  WHERE: src/akb2/quality/intake/extract.py（_extract_via_defuddle:123）
  WHAT:  HTML 写临时文件 → subprocess defuddle parse <tmp> --json -m → 解析 JSON → markdown 正文 _count_words
  WHY:   当前返回 "Defuddle Extracted Title" 模拟数据
  VERIFY: python -m pytest tests/quality/test_intake_extract.py::TestExtractViaDefuddle -q

Step.2
  WHERE: src/akb2/quality/intake/extract.py（_extract_via_scrapling:140）
  WHAT:  import scrapling；自适应用 Fetcher；browser_mode→StealthyFetcher/PlayWrightFetcher
  WHY:   当前返回 "Scrapling adaptive Title" 占位
  VERIFY: python -m pytest tests/quality/test_intake_extract.py::TestExtractViaScrapling -q

Step.3
  WHERE: src/akb2/quality/intake/extract.py（_extract_via_docling:159）
  WHAT:  from docling.document_converter import DocumentConverter；file_bytes 落临时 PDF；convert()→export_to_markdown()
  WHY:   当前 bytes.decode() 当正文
  VERIFY: python -m pytest tests/quality/test_intake_extract.py::TestExtractViaDocling -q

Step.4
  WHERE: src/akb2/quality/intake/extract.py（extract_content:181，新增 HTTP fetcher 入口）
  WHAT:  html is None 且非 PDF 时 httpx 自动抓 HTML；MIN_CONTENT_LENGTH 上调至 200
  WHY:   无 fetcher 入口导致决策树分支 2/3 永远不进
  VERIFY: python -m pytest tests/quality/test_intake_extract.py -q

Step.5
  WHERE: src/akb2/quality/intake/__init__.py
  WHAT:  import gate 探测（importlib，不可用 log warning 不抛）；pyproject 依赖已归 step-6 负责，此步不修改 pyproject.toml
  WHY:   P3 安装≠当前 Python 可导入
  VERIFY: python -m pytest tests/quality/ -q -k "import_gate"
```
