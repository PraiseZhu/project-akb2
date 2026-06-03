"""飞书复用层 — 从 Project Doki 移植的最小集（requests→httpx，去全局依赖）。

仅保留"鉴权 + 发卡片/私信 + markdown→lark_md"，不含周报专用逻辑。
"""
