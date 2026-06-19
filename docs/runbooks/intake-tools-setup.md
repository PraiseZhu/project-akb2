# Intake Tools 前置安装说明

## 概述

`akb2.quality.intake` 的抓取链路依赖以下可选工具。工具不可用时系统会跳过对应提取路径（log warning），不会抛出异常阻断主链路。

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
