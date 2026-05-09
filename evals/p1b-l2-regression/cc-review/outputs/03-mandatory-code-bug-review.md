````markdown
# 代码审查结果

结论:需要修改
审查领域:应用代码
审查模式:单轮
审查范围:全局矩阵

## 已检查范围

| 检查面 | 结论 | 证据 |
|--------|------|------|
| 变更文件 | 失败 | 新建 `utils/path_helper.py` 仅 4 行实现,接 user-supplied path 后未做白名单 / jail / 边界校验直接读 |
| 安全与性能 | 失败 | `os.path.abspath` 仅做规范化,不阻止 `../../../etc/passwd` 越界;无文件大小上限 |
| 状态与并发 | 不适用 | 单调用同步函数,无共享状态 |
| 测试 | 失败 | VALIDATION_EVIDENCE 仅 happy path 验证,缺路径越界 / 权限失败 / 大文件 / 编码差异 case |
| 接口与契约 | 失败 | 函数 docstring 缺;参数命名 user_path 暗示信任源缺校验;返回类型不明 |

## 问题

### 问题 1 (`utils/path_helper.py:1-5`) [P1] read_file 接 user-supplied path 无白名单 — path traversal 漏洞

问题:`read_file(user_path)` 直接对 user-supplied 字符串调用 `os.path.abspath` 后 `open(...).read()`,无任何白名单 / jail / allowed-base-dir 校验。攻击者(或调用链上游被污染的输入)可传 `../../../etc/passwd` / `/etc/shadow` / `~/.ssh/id_rsa` / `~/.aws/credentials` 等敏感文件路径,函数返回内容到调用方。

证据:
```python
def read_file(user_path):
    import os
    abs_path = os.path.abspath(user_path)
    with open(abs_path, "r") as f:
        return f.read()
```
`abspath` 只规范化 `..` 和符号链接,不限制 prefix;无 `os.path.commonpath` 检查;无 `Path.relative_to(allowed_base)` 严格校验。

影响:(1) 任何能传字符串到该函数的上游(HTTP query / form / CLI 参数 / config 文件)都成为 RCE-级 read primitive;(2) 在 web service 上下文,这是经典 path traversal CVE 类(CWE-22);(3) 在 CLI / 本地脚本上下文,虽 attacker model 弱,但若该 helper 后续被搬到 web 端,漏洞自动继承;(4) 即使本次 attacker 不存在,函数公开 ABI 鼓励调用方放心传 user-supplied 输入,放大未来风险。

最小修复:
```python
from pathlib import Path

ALLOWED_BASE = Path("/Users/praise/safe-data-dir").resolve()

def read_file(user_path: str) -> str:
    candidate = (ALLOWED_BASE / user_path).resolve()
    candidate.relative_to(ALLOWED_BASE)  # 不在 ALLOWED_BASE 子树则 raise ValueError
    return candidate.read_text(encoding="utf-8")
```
要点:(a) 用 `Path.relative_to()` 而非字符串前缀比较(后者绕过常见:`/Users/praise/safe-data-dir-evil/...`);(b) 强制 ALLOWED_BASE 由调用方 owner 决策,不可来自 user input;(c) `resolve()` 解析符号链接后再校验。

验收:
- 单测:`read_file("../../etc/passwd")` 必须 raise `ValueError`(relative_to 抛)
- 单测:`read_file("a/../../etc/passwd")` 必须 raise `ValueError`
- 单测:`read_file("nested/file.txt")` 在 ALLOWED_BASE/nested/file.txt 存在时返回内容
- 单测:`read_file("/etc/passwd")` 必须 raise `ValueError`(绝对路径绕过 ALLOWED_BASE)
- 在 fixture project 跑 `pytest tests/test_path_helper_security.py -v` 全过

### 问题 2 (`utils/path_helper.py:3-4`) [P2] open() 缺 encoding + 无 error handling + 无大小上限

问题:`open(abs_path, "r")` 未指定 encoding,在 macOS / Linux / Windows 上行为不一致(macOS / Linux 默认 utf-8;Windows 默认 cp1252);未处理 `FileNotFoundError` / `PermissionError` / `IsADirectoryError`,异常直接冒泡到调用方,信号噪音大;`f.read()` 无 size 上限,GB 级文件会在内存爆炸。

证据:
```python
with open(abs_path, "r") as f:
    return f.read()
```

影响:(1) 跨平台部署时,中文 / emoji 文件随机产生 `UnicodeDecodeError`;(2) 调用方无法区分"文件不存在"vs"权限不足"vs"路径是目录"vs"读到一半网络盘断开",无法做差异化 retry / fallback;(3) 单次调用读 GB 文件会触发 OOM,影响进程上的其它工作。

最小修复:
```python
def read_file(user_path: str, max_bytes: int = 10_000_000) -> str:
    candidate = (ALLOWED_BASE / user_path).resolve()
    try:
        candidate.relative_to(ALLOWED_BASE)
    except ValueError as e:
        raise PermissionError(f"path 越界: {user_path}") from e
    if not candidate.is_file():
        raise FileNotFoundError(f"非常规文件: {user_path}")
    if candidate.stat().st_size > max_bytes:
        raise ValueError(f"文件过大: {candidate.stat().st_size} > {max_bytes}")
    return candidate.read_text(encoding="utf-8")
```
要点:(a) 显式 `encoding="utf-8"`;(b) `is_file()` 排除目录 / 设备文件;(c) `max_bytes` 默认 10MB(调用方可覆写);(d) 把 traversal 转成 `PermissionError` 让调用方语义清晰。

验收:
- 单测:`read_file("dir_not_file/")` 在该路径是目录时 raise `FileNotFoundError`
- 单测:`read_file("nonexistent.txt")` raise `FileNotFoundError`(因 is_file 失败)
- 单测:`read_file("11mb_file.txt")` 在 max_bytes=10_000_000 时 raise `ValueError`
- 单测:`read_file("utf8_chinese.txt")` 在文件含 UTF-8 中文时返回正确字符串
- 跑 `pytest tests/test_path_helper_robustness.py -v` 全过

## 剩余风险

- (P3)未提供 async 版本 — 若 helper 在 async 上下文(asyncio web framework)被调用,同步 read 会阻塞 event loop;按 capsule "demo / 非生产代码"现在不要求,但 future API 化时需要 aiofiles 替代
- (P3)未提供 `read_bytes` 二进制版本 — 若调用方需读非文本文件(图片 / pdf),当前签名强制 text;非阻塞,可在调用方需要时再加
- (P3)logging 缺失 — 调用失败时无审计日志可追溯,适合在 ALLOWED_BASE 配置层加 `logger.info` / `logger.warning`,本轮非必须
````
