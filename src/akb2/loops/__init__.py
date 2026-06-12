"""AKB2 Loop 工程 — 循环骨架（账本/签名/隔离区/适配卡契约）。

本包提供循环 7 契约骨架的核心模块：隔离区路径约定、幂等账本追加、故障签名记录。
不包含修复 agent 行为引擎（Plan 4 交付）。
"""

from __future__ import annotations
