"""harvest 链路:日常会话收割器的 skill 契约面。

本包承载「收割昨天」skill 的可机器验证契约:
- template.py — SKILL.md submit 命令模板提取器(唯一标记块,零/多失败)
- flow.py    — 确认闸编排 + 交互状态机(proposed → confirmed → submitted)

真正的落盘 assembler(python3 -m akb2.harvest)与 schema 由并行波次
(sc-asm-cli / sc-asm-schema)实现,本包不重复定义。
"""
