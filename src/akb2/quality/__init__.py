"""AKB2 Quality 模块 — 准入、提炼、路由、修复。

准入: admission.py（三硬判定，二值无连续分数）
提炼: distill.py（DeepSeek 两段式：结构化抽取 JSON → 模板成稿）
路由: llm_router.py（OpenAI 兼容端点，三族模型路由）
"""
