"""AKB2 Quality Intake — 抓取与 raw 锚点化（循环 1 段 1-2）。

模块:
    discovery:    RSS/Atom 发现 + 指纹去重 + 产量隔离
    extract:      defuddle → Scrapling → Docling 决策树
    raw_archiver: raw 文件锚点化归档（fail-closed）
    source_validator: 逐源验证 CLI
"""

from __future__ import annotations

import importlib.util
import logging

logger = logging.getLogger(__name__)


def _check_tool_available(name: str) -> bool:
    """探测 Python 包是否可在当前环境导入。

    不抛异常；不可用时记录 warning 并返回 False。
    """
    available = importlib.util.find_spec(name) is not None
    if not available:
        logger.warning(
            "%s not available in current Python env; related extraction will be skipped",
            name,
        )
    return available
