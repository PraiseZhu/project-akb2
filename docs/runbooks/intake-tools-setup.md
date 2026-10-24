# Intake Tools 前置安装说明

> **2026-10-24 起无代码调用**：这些工具原先服务主链路的 intake 抓取（`quality/intake/extract.py` 等），主链路已删除，scrapling / docling 也已移出 `pyproject.toml`。CI 仍会安装 defuddle（改 CI 需另行授权），§6 的包身份说明留作参考；新主链路重建时按实际用到的工具重写本文。

## 概述

`akb2.quality.intake` 的抓取链路依赖以下工具。

- **§1–§3（Playwright / Scrapling / Docling）是可选工具**：不可用时系统跳过对应提取路径（log warning），不抛异常阻断主链路。
- **§6（defuddle CLI）不是可选的**：它是抽取决策树的第一分支，也是 `scripts/deploy-mac-mini.sh` 的 B-dep **硬门**（缺失即 fail-closed 中止部署），并且 CI `tests` job 强制要求它存在。装法与钉版本理由见 §6。

---

## 1. Playwright 浏览器（scrapling browser_mode 前置）

scrapling 的 `browser_mode=True` 需要 Playwright 浏览器已安装。

### 安装步骤

```bash
# 安装 playwright Python 包（如尚未安装）
pip install playwright

# 安装 Chromium 浏览器（推荐，最轻量）
playwright install chromium

# 或通过 python -m 调用（环境隔离时使用）
python -m playwright install chromium

# 验证安装
python -m playwright --version
```

### 说明

- `playwright install` 默认安装 chromium + firefox + webkit 三个浏览器，仅需 chromium 时加参数节省磁盘
- Mac mini 部署时需在目标用户环境下执行上述命令（不可仅在 venv 内安装包而不运行 `install`）
- CI/CD 环境需在 pipeline 步骤中显式调用 `playwright install chromium`

---

## 2. Scrapling

```bash
pip install "scrapling>=0.4.8"

# 初次使用需安装浏览器（见上方 Playwright 章节）
# 可选：安装默认依赖
python -c "import scrapling; print(scrapling.__version__)"
```

---

## 3. Docling

```bash
pip install "docling>=2.0.0"

# 验证
python -c "import docling; print('docling OK')"
```

docling 用于 PDF / Office 文档提取，体积较大（含模型权重下载），首次运行会自动下载。

---

## 4. 可用性探测

`_check_tool_available(name)` 函数（`akb2.quality.intake`）在运行时探测包是否可导入：

```python
from akb2.quality.intake import _check_tool_available

scrapling_ok = _check_tool_available("scrapling")   # True / False
docling_ok   = _check_tool_available("docling")     # True / False
```

不可用时记录 WARNING，不抛出异常，提取链路降级到其他方法。

---

## 5. 依赖声明

以上工具已在 `pyproject.toml` `[project].dependencies` 中声明：

```
scrapling>=0.4.8
docling>=2.0.0
```

在受管环境（如 `pip install -e .`）中会自动安装；**但 Playwright 浏览器二进制**不会自动安装，需手动执行 `playwright install chromium`。

**defuddle 不在其中** —— 它是 npm 包提供的外部 CLI，pip 装不到，见 §6。

---

## 6. defuddle CLI（外部 Node 依赖，非可选）

### 6.1 它是什么、谁在调它

抽取决策树的第一分支（静态页优先走它）。调用点是
`src/akb2/quality/intake/extract.py::_extract_via_defuddle`，执行的命令逐字是：

```bash
defuddle parse <html-tmpfile-或-url> --json -m
```

失败（含 `FileNotFoundError`：CLI 不在 PATH）时该函数返回空 dict，链路降级到 Scrapling。
所以**缺 defuddle 不会崩，只会静默降级** —— 这正是它容易被忽视、又必须显式门控的原因。

### 6.2 安装（钉死版本）

```bash
npm install -g defuddle-cli@0.7.0

# 验证：应打印 0.1.0（见 6.3 的版本号陷阱，这是正常的）
defuddle --version
defuddle parse --help
```

Node 版本：**24.x（Active LTS，Krypton）**。CI 写死在 24；本地 Node 26 亦实测可用。

### 6.3 包身份陷阱（踩过，务必读）

npm 上有**两个**包都提供名为 `defuddle` 的可执行文件，且版本号语义互不相干：

| npm 包 | 提供 `defuddle` bin | `defuddle --version` 输出 | 备注 |
|--------|--------------------|--------------------------|------|
| `defuddle-cli@0.7.0` | 是（`dist/index.js`） | `0.1.0` | **当前钉死的这个**。已被上游标记 deprecated，但 0.7.0 是该包终版，内容冻结 |
| `defuddle@0.19.1` | 是（`dist/cli.js`） | 随包版本 | 非 deprecated，长期迁移目标（见 6.5） |
| `defuddle@0.1.0` | **否**（`bin: null`） | —— | **陷阱**：装了也没有 `defuddle` 命令 |

**关键**：`defuddle-cli@0.7.0` 的 `dist/index.js` 里写死了 `.version('0.1.0')`（字面量，从未随包版本更新）。
所以 `defuddle --version` 打印的 `0.1.0` **不是包版本**。

绝不要根据 `--version` 的输出去装包：`npm install -g defuddle@0.1.0` 会成功，
但它装的是那个 `bin: null` 的库包，装完 PATH 上依然没有 `defuddle`。
要确认真实来源，用：

```bash
readlink "$(command -v defuddle)"   # → .../node_modules/defuddle-cli/dist/index.js
```

### 6.4 为什么钉版本

1. **防上游改输出格式把 CI 弄红**。`_extract_via_defuddle` 直接消费 CLI 的 JSON 字段
   （`title` / `author` / `published` / `content` / `wordCount`），字段或正文切分规则一变，
   `tests/quality/test_intake_extract.py` 里断言"真实抽取输出"的 5 项就会红，
   而这与本仓代码改动无关。
2. **CI 必须复现本地绿基线**。钉的就是本地那份产物，CI 与本地不引入第二个变量。

### 6.5 迁移到 `defuddle` 本体（待办，非本次）

`defuddle-cli` 已 deprecated，长期应迁到 `defuddle@0.19.x`。
`defuddle@0.19.1` 已实测通过全部 39 项 `tests/quality/test_intake_extract.py`，
即当前调用契约（`parse ... --json -m` + 上述 JSON 字段）在两者间等价。
迁移时需同步改三处：本节、`.github/workflows/ci.yml` 的 npm 安装步骤、
`docs/mac-mini-deployment-notes.md` 的 B3a 行。

### 6.6 CI 强制开关：`AKB2_REQUIRE_DEFUDDLE`

依赖真实 defuddle 的测试不允许"悄悄跳过"——那会让 CI 变绿而覆盖归零。契约：

| `AKB2_REQUIRE_DEFUDDLE` | defuddle 缺失时的行为 |
|-------------------------|----------------------|
| `"1"` | 测试**失败**（不得 skip），报错指明「CI 必须装 defuddle，见 `.github/workflows/ci.yml`」 |
| 未设 / 非 `"1"` | 测试 **skip**，skip reason 指明「本地未装 defuddle；装法见 `docs/runbooks/intake-tools-setup.md` §6」 |

CI 侧在 `.github/workflows/ci.yml` 的 `tests` job → `pytest` 步骤上设 `AKB2_REQUIRE_DEFUDDLE: "1"`。
本地开发者不设它即可（缺 defuddle 时 skip，不挡本地跑测试）；
但**本地要跑全量真值验证时应先装 defuddle**，否则拿不到这 5 项的覆盖。
