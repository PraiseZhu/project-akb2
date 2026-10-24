"""AKB2 Quality 模块。

主链路（循环1 主质量管线 + 循环2 X 采集）已于 2026-10-24 删除，待重建。
这里只留保留链路仍在用的部分：
    llm_router.py: OpenAI 兼容端点，三族模型路由（GitHub 早报在用）
    intake/:       raw 归档与身份契约（harvest 在用）
"""
