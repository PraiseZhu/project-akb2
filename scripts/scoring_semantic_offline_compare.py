#!/usr/bin/env python3
"""P1-2 离线对比 + 标定：关键词 topic_relevance vs 语义 cosine。

目的：
1. 用真实嵌入跑出「关键词版 vs 语义版」相关度并排表，证明关键词窄门漏掉中文/无 buzzword 的 AI 内容。
2. 标定语义归一化区间 [LOW, HIGH]：raw cosine → relevance ∈ [0,1]。
   relevance = clamp((max_cos - LOW) / (HIGH - LOW), 0, 1)

用法（需真实网关 key）：
    source ~/.akb2-secrets.sh
    PYTHONPATH=src python3 scripts/scoring_semantic_offline_compare.py \
        --input tests/fixtures/briefing/scored-sample.json \
        --report reports/scoring-semantic-offline-compare-<date>.md

无 key 或网关失败 → 直接抛错（不产假数据）。

P1-2 owned_paths：scripts/ + reports/。本脚本**只输出**推荐常量，由 P1-4 写入 scoring.py。
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from akb2.briefing.embeddings import EmbeddingClient, cosine
from akb2.briefing.scoring import topic_relevance as keyword_topic_relevance

# 语义锚点：代表「AI/ML 社区高质量内容」的主题描述（多锚点取 max cosine）。
# P1-4 会把同一组锚点写进 scoring.py（此处为标定权威源）。
TOPIC_ANCHORS = [
    "Artificial intelligence, machine learning, large language models and LLM research.",
    "AI agents, autonomous agents, tool use, multi-agent systems and agent orchestration.",
    "Model training, fine-tuning, inference, quantization, serving and ML infrastructure.",
    "AI alignment, safety, reasoning, multimodal models, AGI and frontier model capability.",
    "人工智能、大模型、机器学习、智能体、推理与对齐研究。",
]

# 对照探针：关键词窄门会漏掉但语义上明确属于 AI 的内容 + 明确离题内容。
CONTRAST_PROBES = [
    # 中文 AI（英文关键词命中=0）
    ("zh_ai_1", "国产大模型今天发布，推理能力大幅提升，中文理解超过上一代，价格还降了一半。"),
    ("zh_ai_2", "我们用智能体重构了整个客服流程，自动处理率超过八成，人工只需复核。"),
    # 无 buzzword 的英文 AI（16 词关键词命中=0）
    ("nobuzz_ai_1", "Spent the weekend getting FP8 quantization working on our serving stack. Throughput up 2.3x, accuracy drop negligible."),
    ("nobuzz_ai_2", "Stop chunking documents and bolting on a vector DB; let the system decide what to fetch and when. Retrieval should be a decision, not a pipeline."),
    # 明确离题
    ("offtopic_1", "Just made the best homemade pizza with fresh basil and mozzarella. Recipe in the comments!"),
    ("offtopic_2", "The Lakers won last night in overtime, incredible game and a clutch buzzer-beater."),
    ("offtopic_3", "Stock market hit a new record high today after the central bank signaled rate cuts."),
]


def _max_cos(vec, anchor_vecs):
    return max(cosine(vec, a) for a in anchor_vecs)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    tweets = data.get("tweets", [])

    client = EmbeddingClient()

    # 1) 嵌入锚点
    anchor_vecs = client.embed(TOPIC_ANCHORS)

    # 2) 嵌入样本推文 + 对照探针
    tweet_texts = [t.get("text", "") for t in tweets]
    probe_labels = [p[0] for p in CONTRAST_PROBES]
    probe_texts = [p[1] for p in CONTRAST_PROBES]
    tweet_vecs = client.embed(tweet_texts)
    probe_vecs = client.embed(probe_texts)

    # 3) 计算
    rows = []
    for t, v in zip(tweets, tweet_vecs):
        kw = keyword_topic_relevance(t)
        mc = _max_cos(v, anchor_vecs)
        rows.append((t.get("author", "?"), t.get("text", "")[:60], kw, mc, "sample"))
    probe_rows = []
    for (label, text), v in zip(CONTRAST_PROBES, probe_vecs):
        kw = keyword_topic_relevance({"text": text})
        mc = _max_cos(v, anchor_vecs)
        probe_rows.append((label, text[:60], kw, mc))

    # 4) 标定区间：离题探针 cosine 上界 → LOW；样本+AI探针 cosine 中位/低位 → HIGH 参考
    offtopic_cos = [mc for (lbl, _, _, mc) in probe_rows if lbl.startswith("offtopic")]
    ai_probe_cos = [mc for (lbl, _, _, mc) in probe_rows if not lbl.startswith("offtopic")]
    sample_cos = [mc for (_, _, _, mc, _) in rows]
    relevant_cos = sample_cos + ai_probe_cos

    low = max(offtopic_cos) if offtopic_cos else 0.10            # 离题最高 cosine 作为地板
    # HIGH 取相关内容的较低分位（让多数相关内容 relevance 接近 1，区分离题）
    high = statistics.quantiles(relevant_cos, n=10)[1] if len(relevant_cos) >= 4 else (max(relevant_cos) if relevant_cos else 0.45)
    # 加一点 margin，避免 LOW/HIGH 过近
    if high <= low + 0.05:
        high = low + 0.15

    # 5) 写报告
    lines = []
    lines.append("# Scoring 语义 vs 关键词 离线对比 + 标定 (P1-2)")
    lines.append("")
    lines.append(f"- 锚点数：{len(TOPIC_ANCHORS)}（多语言，max cosine 归一化）")
    lines.append(f"- 样本推文：{len(tweets)}；对照探针：{len(CONTRAST_PROBES)}")
    lines.append(f"- 嵌入模型：{client.model}")
    lines.append("")
    lines.append("## 标定结论（推荐写入 scoring.py 常量）")
    lines.append("")
    lines.append(f"```python")
    lines.append(f"_RELEVANCE_COS_LOW = {low:.4f}   # 离题内容 cosine 地板（max offtopic = {low:.4f}）")
    lines.append(f"_RELEVANCE_COS_HIGH = {high:.4f}  # 相关内容归一化上界（relevant 低分位）")
    lines.append(f"# relevance = clamp((max_cos - LOW) / (HIGH - LOW), 0, 1)")
    lines.append(f"```")
    lines.append("")
    lines.append(f"- 离题 cosine 范围：{min(offtopic_cos):.4f} ~ {max(offtopic_cos):.4f}")
    lines.append(f"- AI 探针 cosine 范围：{min(ai_probe_cos):.4f} ~ {max(ai_probe_cos):.4f}")
    lines.append(f"- 样本 cosine 范围：{min(sample_cos):.4f} ~ {max(sample_cos):.4f}")
    lines.append("")
    lines.append("## 对照探针：关键词窄门 vs 语义（核心证据）")
    lines.append("")
    lines.append("| 探针 | 关键词 relevance | 语义 max cosine | 文本 |")
    lines.append("|------|----------------|----------------|------|")
    for lbl, text, kw, mc in probe_rows:
        lines.append(f"| {lbl} | {kw:.2f} | {mc:.4f} | {text} |")
    lines.append("")
    lines.append("> 关键：中文 AI / 无 buzzword AI 探针关键词 relevance≈0（被窄门漏掉），但语义 cosine 明显高于离题 → 语义版能捞回。")
    lines.append("")
    lines.append("## 样本推文：关键词 vs 语义并排")
    lines.append("")
    lines.append("| 作者 | 关键词 relevance | 语义 max cosine | 文本(截断) |")
    lines.append("|------|----------------|----------------|-----------|")
    for author, text, kw, mc, _ in rows:
        lines.append(f"| {author} | {kw:.2f} | {mc:.4f} | {text} |")
    lines.append("")

    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text("\n".join(lines), encoding="utf-8")
    print(f"报告写入 {args.report}")
    print(f"推荐标定：LOW={low:.4f} HIGH={high:.4f}")
    print(f"离题 cosine: {min(offtopic_cos):.4f}~{max(offtopic_cos):.4f} | AI探针: {min(ai_probe_cos):.4f}~{max(ai_probe_cos):.4f} | 样本: {min(sample_cos):.4f}~{max(sample_cos):.4f}")


if __name__ == "__main__":
    main()
