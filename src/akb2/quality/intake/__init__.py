"""AKB2 Quality Intake — 抓取与 raw 锚点化（循环 1 段 1-2）。

模块:
    discovery:    RSS/Atom 发现 + 指纹去重 + 产量隔离
    extract:      defuddle → Scrapling → Docling 决策树
    raw_archiver: raw 文件锚点化归档（fail-closed）
    source_validator: 逐源验证 CLI
"""

from __future__ import annotations
