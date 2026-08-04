"""plist 解析的**单一咽喉** —— 全仓所有 plist 解析点共用同一注释策略。

为什么必须只有一份实现（2026-08-04，R2 blocker）：
本仓此前有 4 个 plist 解析点，分成了两套注释策略 ——

    宽（strict 失败后剥 XML 注释重解析）：
        scripts/verify-launchd-parity.sh  _load_plist（读 installed plist）
    严（裸 plistlib，注释里一个裸 `--` 就拒）：
        scripts/verify-launchd-parity.sh  _render_template（渲染仓库模板后解析）
        scripts/lib/launchctl.sh          akb2_plist_identity（派生 Label/程序/调度锚）
        scripts/lib/launchctl.sh          akb2_assert_render_clean（安装前 lint 门）

后果实测：`launchd/com.akb2.feishu-sync.plist` 与 `com.akb2.inflow-sentinel.plist`
的文档注释里含裸 `--`（`--dry-run` / `--target dm` 这类旗标名），于是
  · `bootstrap_launchd.sh --render-only --plist-src <这两个模板>` → exit=2
    「无法从 plist 解析 Label」，**仓库自带的模板装不上**（对照组 briefing exit=0）；
  · parity 对 feishu-sync 报假 `DRIFT reason=parse_error=not well-formed`，
    并把该 job 真实的 `_diff_dicts` 差异整条吞进 except 分支。

方向为什么是"宽"而不是"给模板改旗标写法"：**生产消费方比 expat 宽**。
launchd 用 CoreFoundation 解析 plist，其命令行同族 `plutil -lint` 实测对
launchd/ 下 9 个模板（含上面两个）一律 `OK`；拒解的只有 Python 的 expat
（XML 规范确实禁注释内 `--`，CoreFoundation 不较真）。注释不参与 plist 的
实际结构，所以剥掉注释后解析出的字典与 launchd 看到的完全一致 —— 咽喉判的是
「launchd 能不能加载」，不是「是否满足 XML 注释的严格字面规范」。

同时**不放宽真实的结构错误**：只有 `ExpatError`（XML 不良构）才触发剥注释重试，
且重试后仍失败即原样抛出；`InvalidFileException`（如裸 `<string>` 片段、不是
plist 的内容）不进重试路径。`akb2_assert_render_clean` 在 macOS 上仍叠加
`plutil -lint`，门的强度只增不减。

**新增任何 plist 解析点都必须调本模块**，不得再复制第三份策略；
`tests/test_plist_parse_chokepoint.py` 同时守行为一致与"没有第二份实现"。
"""

from __future__ import annotations

import plistlib
import re
from pathlib import Path
from typing import Any
from xml.parsers.expat import ExpatError

# XML 注释块。DOTALL：注释常跨多行（模板头部的说明块就是）。
_XML_COMMENT_RE = re.compile(rb"<!--.*?-->", re.DOTALL)


def strip_xml_comments(raw: bytes) -> bytes:
    """剥掉 `<!-- ... -->` 块。注释不参与 plist 结构，剥掉不改变解析结果。"""
    return _XML_COMMENT_RE.sub(b"", raw)


def parse_plist_bytes(raw: bytes) -> Any:
    """解析 plist 字节：先 strict，仅 XML 不良构时剥注释重试一次。

    抛出与 `plistlib.loads` 相同的异常类型（重试后仍失败即原样冒泡），
    调用方的 fail-closed 语义不变。
    """
    try:
        return plistlib.loads(raw)
    except ExpatError:
        # 唯一的宽容点：注释内容让 expat 拒解（裸 `--` 等），而 launchd 接受。
        return plistlib.loads(strip_xml_comments(raw))


def parse_plist_file(path: str | Path) -> Any:
    """按路径解析 plist（读字节后走 `parse_plist_bytes`）。"""
    return parse_plist_bytes(Path(path).read_bytes())


def parse_plist_text(text: str) -> Any:
    """按文本解析 plist（渲染后的模板走这条：文本已在内存里，不落盘）。"""
    return parse_plist_bytes(text.encode("utf-8"))
