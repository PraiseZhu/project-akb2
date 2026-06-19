# Step 6: 工程门 + import gate

GD_STANDARD: Project GD/prompts/gd-review-standard.md
TEMPLATE_KIND: gd-step-plan

日期: 2026-06-14 | 状态: draft

---

## 1. 目标链

```text
TASK_GOAL: pyproject.toml 补 scrapling/docling；import gate 探测（不可用 log warning 不抛）；全量 pytest + ruff 绿；playwright 浏览器安装写 runbook。
```

REVIEW_DOMAIN: ai_infra
REVIEW_FOCUS: `import gate 可用性探测(P3); scrapling/docling pyproject 声明; pytest 全绿无真网调用`

---

## 3. 成功标准（SC）

- [ ] SC-11（工程门）: 抓取链路全量测试 + lint 绿；integration 标记 deselected；无真网调用
  - verify (method: command): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python -m pytest tests/quality/ tests/loops/ -q && ruff check src/akb2/quality/intake/ tests/quality/ && echo PASS`
  - expect: PASS; 全部测试通过，ruff 0 issues，integration 标记 4 deselected

---

## 6. 实现步骤

```text
Step.1
  WHERE: pyproject.toml（dependencies 列表）
  WHAT:  补 scrapling>=0.4.8 和 docling>=2.0.0 到 [project] dependencies
  WHY:   P3 安装≠pyproject 声明；依赖透明化
  VERIFY: python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); deps=d['project']['dependencies']; assert any('scrapling' in dep for dep in deps); assert any('docling' in dep for dep in deps); print('PASS')"

Step.2
  WHERE: src/akb2/quality/intake/__init__.py
  WHAT:  _check_tool_available(name: str) 用 importlib.util.find_spec 探测；不可用时 logging.warning 不抛；defuddle 用 shutil.which 探测 CLI
  WHY:   P3 已安装≠当前 Python env 可导入
  VERIFY: python -m pytest tests/quality/ -q -k "import_gate"

Step.3
  WHERE: docs/runbooks/intake-deploy.md（新建）
  WHAT:  playwright 浏览器安装前置（playwright install chromium）；scrapling browser_mode 依赖声明
  WHY:   scraping browser_mode 需 playwright 浏览器，Mac mini 部署前置
  VERIFY: test -f docs/runbooks/intake-deploy.md && grep -q "playwright install" docs/runbooks/intake-deploy.md && echo PASS

Step.4
  WHERE: tests/quality/ + tests/loops/（全量）
  WHAT:  python -m pytest tests/quality/ tests/loops/ -q 全绿；ruff check src/akb2/quality/intake/ tests/quality/ 0 issues
  WHY:   SC-11 工程门
  VERIFY: python -m pytest tests/quality/ tests/loops/ -q && ruff check src/akb2/quality/intake/ tests/quality/ && echo PASS
```
