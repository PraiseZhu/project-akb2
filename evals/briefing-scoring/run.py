"""P1-7 打分排序 eval harness：关键词版 vs 语义版排序质量量化对比。

用途（SC-8-1 / SC-8）：
  对标注集 fixtures/labeled.json（label=1 高质量 AI 应排前；label=0 离题/低质应排后）
  分别用关键词版 ScoreEngine() 和语义版 ScoreEngine(embed_client=EmbeddingClient())
  打分排序，算 precision@K / nDCG@K / 关键词受害者救回数，判语义版是否 ≥ 关键词版。

排序质量指标：
  precision@K  —— top-K 中 label=1 的占比（K 默认 = 正样本数）
  nDCG@K       —— 以 label 为 relevance 的归一化折损累计增益
  victim_rescue@K —— 「关键词受害者」（label=1 但关键词 topic_relevance=0，
                    多为中文 AI / 无 buzzword AI）进 top-K 的数量；语义版的核心收益

验收（SC-8）：语义版 nDCG@K ≥ 关键词版 且 precision@K ≥ 关键词版 → exit 0；否则 exit 1。

语义版需真实嵌入（网关 AI_GATEWAY_API_KEY）。run.py 显式探测嵌入可用性，
嵌入不可用时 **报错退出（exit 2）**，绝不静默回退成「关键词 vs 关键词」假对比。

CLI:
  source ~/.akb2-secrets.sh   # 提供 AI_GATEWAY_API_KEY
  PYTHONPATH=src python3 evals/briefing-scoring/run.py \
    [--labeled <path>] [--report <path>] [--k <int>]

  --no-semantic   仅跑关键词版（冒烟用，不做验收，exit 0）
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path

# 允许从仓库根直接 `python3 evals/briefing-scoring/run.py` 运行：补 src 到 path
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from akb2.briefing.scoring import ScoreEngine, _topic_relevance_keywords  # noqa: E402

_DEFAULT_LABELED = Path(__file__).resolve().parent / "fixtures" / "labeled.json"


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------

def load_labeled(path: str | Path) -> list[dict]:
    """读标注集，返回 tweets 列表。每条须含 label ∈ {0,1}。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    tweets = data.get("tweets", [])
    if not tweets:
        raise ValueError(f"标注集为空或缺 tweets：{path}")
    for i, t in enumerate(tweets):
        if t.get("label") not in (0, 1):
            raise ValueError(f"第 {i} 条缺合法 label（须 0/1）：{t.get('text','')[:40]}")
    return tweets


# ---------------------------------------------------------------------------
# 排序质量指标（纯函数，便于单测）
# ---------------------------------------------------------------------------

def rank_by_score(scored: list[dict]) -> list[dict]:
    """按 score 降序排（稳定排序：同分保持原序）。"""
    return sorted(scored, key=lambda t: t["score"], reverse=True)


def precision_at_k(ranked: list[dict], k: int) -> float:
    """top-K 中 label=1 的占比。"""
    if k <= 0:
        return 0.0
    topk = ranked[:k]
    hits = sum(1 for t in topk if t.get("label") == 1)
    return hits / k


def dcg_at_k(labels: list[int], k: int) -> float:
    """DCG@K：rel_i / log2(i+1)，i 从 1 计。"""
    dcg = 0.0
    for i, rel in enumerate(labels[:k], start=1):
        if rel:
            dcg += rel / math.log2(i + 1)
    return dcg


def ndcg_at_k(ranked: list[dict], k: int) -> float:
    """nDCG@K，relevance = label。"""
    labels = [int(t.get("label", 0)) for t in ranked]
    ideal = sorted(labels, reverse=True)
    idcg = dcg_at_k(ideal, k)
    if idcg == 0.0:
        return 0.0
    return dcg_at_k(labels, k) / idcg


def keyword_victim_ids(tweets: list[dict]) -> set[int]:
    """「关键词受害者」：label=1 但关键词 topic_relevance == 0（关键词窄门完全漏掉的 AI 内容）。

    用 id() 不稳，故返回基于 (author,text) 的标识集。
    """
    victims = set()
    for t in tweets:
        if t.get("label") == 1 and _topic_relevance_keywords(t) == 0.0:
            victims.add((t.get("author"), t.get("text")))
    return victims


def victim_rescue_at_k(ranked: list[dict], victim_keys: set, k: int) -> int:
    """top-K 中包含多少个关键词受害者（语义版应显著高于关键词版）。"""
    topk = ranked[:k]
    return sum(1 for t in topk if (t.get("author"), t.get("text")) in victim_keys)


# ---------------------------------------------------------------------------
# 嵌入可用性探测（fail-visibly，不静默回退）
# ---------------------------------------------------------------------------

def make_semantic_engine():
    """构造语义版 ScoreEngine；嵌入不可用时抛错（绝不静默降级关键词版）。"""
    from akb2.briefing.embeddings import EmbeddingClient

    client = EmbeddingClient()
    # 显式探测一次：网关不可达 / 无 key → 抛错，调用方 exit 2
    client.embed(["probe: artificial intelligence and large language models"])
    return ScoreEngine(embed_client=client)


# ---------------------------------------------------------------------------
# 评估主流程
# ---------------------------------------------------------------------------

def evaluate(tweets: list[dict], k: int, *, semantic: bool, semantic_engine=None) -> dict:
    """跑关键词版（+可选语义版）并算全部指标，返回结构化结果。

    semantic_engine 可注入（测试用 FakeEmbed）；None 时走真实 make_semantic_engine()。
    """
    victim_keys = keyword_victim_ids(tweets)

    kw_scored = ScoreEngine().score_batch([dict(t) for t in tweets])
    kw_ranked = rank_by_score(kw_scored)
    metrics = {
        "k": k,
        "n_total": len(tweets),
        "n_positive": sum(1 for t in tweets if t.get("label") == 1),
        "n_victims": len(victim_keys),
        "keyword": {
            "precision_at_k": precision_at_k(kw_ranked, k),
            "ndcg_at_k": ndcg_at_k(kw_ranked, k),
            "victim_rescue_at_k": victim_rescue_at_k(kw_ranked, victim_keys, k),
        },
        "keyword_ranked": kw_ranked,
    }

    if semantic:
        sem_engine = semantic_engine if semantic_engine is not None else make_semantic_engine()
        sem_scored = sem_engine.score_batch([dict(t) for t in tweets])
        # fail-visibly：语义版若静默回退（所有受害者 topic_relevance 仍为 0）则判定嵌入未生效
        sem_victim_rel = [
            d["dimensions"]["topic_relevance"]
            for d in sem_scored
            if (d.get("author"), d.get("text")) in victim_keys
        ]
        if sem_victim_rel and max(sem_victim_rel) == 0.0:
            raise RuntimeError(
                "语义版所有关键词受害者 topic_relevance 仍为 0 —— 嵌入疑似静默回退，拒绝产出假对比"
            )
        sem_ranked = rank_by_score(sem_scored)
        metrics["semantic"] = {
            "precision_at_k": precision_at_k(sem_ranked, k),
            "ndcg_at_k": ndcg_at_k(sem_ranked, k),
            "victim_rescue_at_k": victim_rescue_at_k(sem_ranked, victim_keys, k),
        }
        metrics["semantic_ranked"] = sem_ranked

    return metrics


def acceptance(metrics: dict) -> bool:
    """SC-8：语义版 nDCG@K ≥ 关键词版 且 precision@K ≥ 关键词版。"""
    if "semantic" not in metrics:
        return False
    kw, sem = metrics["keyword"], metrics["semantic"]
    return (
        sem["ndcg_at_k"] >= kw["ndcg_at_k"]
        and sem["precision_at_k"] >= kw["precision_at_k"]
    )


# ---------------------------------------------------------------------------
# 报告渲染
# ---------------------------------------------------------------------------

def _ranked_table(ranked: list[dict], k: int) -> str:
    lines = ["| # | label | score | topic_rel | author | text |",
             "|---|-------|-------|-----------|--------|------|"]
    for i, t in enumerate(ranked[:k], start=1):
        rel = t.get("dimensions", {}).get("topic_relevance", 0.0)
        txt = (t.get("text", "")[:40]).replace("|", "/").replace("\n", " ")
        lines.append(
            f"| {i} | {t.get('label')} | {t['score']:.4f} | {rel:.3f} | {t.get('author','')} | {txt} |"
        )
    return "\n".join(lines)


def render_report(metrics: dict, *, run_date: str, accepted: bool) -> str:
    kw = metrics["keyword"]
    has_sem = "semantic" in metrics
    k = metrics["k"]

    out = [
        f"# 打分排序 eval 报告 — {run_date}",
        "",
        "GOAL_SOURCE: Project AKB2 — 社交日报系统（P1-7 harness / P1-8 验收）",
        f"标注集：{metrics['n_total']} 条（label=1: {metrics['n_positive']}，关键词受害者: {metrics['n_victims']}）",
        f"K = {k}",
        "",
        "## 指标对比（语义版 vs 关键词版）",
        "",
        "| 指标 | 关键词版 | 语义版 | 语义优于? |",
        "|------|----------|--------|-----------|",
    ]
    if has_sem:
        sem = metrics["semantic"]
        def cmp(a, b):
            return "✓" if b >= a else "✗"
        out += [
            f"| precision@{k} | {kw['precision_at_k']:.4f} | {sem['precision_at_k']:.4f} | {cmp(kw['precision_at_k'], sem['precision_at_k'])} |",
            f"| nDCG@{k} | {kw['ndcg_at_k']:.4f} | {sem['ndcg_at_k']:.4f} | {cmp(kw['ndcg_at_k'], sem['ndcg_at_k'])} |",
            f"| victim_rescue@{k} | {kw['victim_rescue_at_k']}/{metrics['n_victims']} | {sem['victim_rescue_at_k']}/{metrics['n_victims']} | {cmp(kw['victim_rescue_at_k'], sem['victim_rescue_at_k'])} |",
            "",
            f"**验收（SC-8）**：{'✅ APPROVED — 语义版 nDCG 与 precision 均 ≥ 关键词版' if accepted else '❌ REQUIRES_CHANGES — 语义版未达 ≥ 关键词版'}",
        ]
    else:
        out += [
            f"| precision@{k} | {kw['precision_at_k']:.4f} | (未跑) | — |",
            f"| nDCG@{k} | {kw['ndcg_at_k']:.4f} | (未跑) | — |",
            "",
            "> --no-semantic 模式：仅关键词版冒烟，未做验收对比。",
        ]

    out += ["", f"## 关键词版 top-{k} 排序", "", _ranked_table(metrics["keyword_ranked"], k)]
    if has_sem:
        out += ["", f"## 语义版 top-{k} 排序", "", _ranked_table(metrics["semantic_ranked"], k)]
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打分排序 eval harness（关键词版 vs 语义版）")
    parser.add_argument("--labeled", default=str(_DEFAULT_LABELED), help="标注集 JSON 路径")
    parser.add_argument("--report", default=None, help="报告输出路径（默认 reports/scoring-eval-<date>.md）")
    parser.add_argument("--k", type=int, default=None, help="top-K（默认 = 正样本数）")
    parser.add_argument("--no-semantic", action="store_true", help="仅跑关键词版冒烟，不做验收")
    args = parser.parse_args(argv)

    try:
        tweets = load_labeled(args.labeled)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"EVAL_ERROR: 标注集加载失败: {e}", file=sys.stderr)
        return 2

    k = args.k if args.k else sum(1 for t in tweets if t.get("label") == 1)
    run_date = date.today().isoformat()

    try:
        metrics = evaluate(tweets, k, semantic=not args.no_semantic)
    except Exception as e:
        # 嵌入不可用 / 静默回退 → fail visibly
        print(f"EVAL_ERROR: 评估失败（嵌入不可用或静默回退）: {e}", file=sys.stderr)
        return 2

    accepted = acceptance(metrics) if not args.no_semantic else True
    report_path = Path(args.report) if args.report else (_REPO_ROOT / "reports" / f"scoring-eval-{run_date}.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(metrics, run_date=run_date, accepted=accepted), encoding="utf-8")

    kw = metrics["keyword"]
    print(f"标注集 {metrics['n_total']} 条 / K={k} / 关键词受害者 {metrics['n_victims']}")
    print(f"关键词版: precision@{k}={kw['precision_at_k']:.4f} nDCG@{k}={kw['ndcg_at_k']:.4f} victim_rescue={kw['victim_rescue_at_k']}")
    if "semantic" in metrics:
        sem = metrics["semantic"]
        print(f"语义版:   precision@{k}={sem['precision_at_k']:.4f} nDCG@{k}={sem['ndcg_at_k']:.4f} victim_rescue={sem['victim_rescue_at_k']}")
    print(f"报告 → {report_path}")

    if args.no_semantic:
        return 0
    if accepted:
        print("EVAL_RESULT: APPROVED（语义版 ≥ 关键词版）")
        return 0
    print("EVAL_RESULT: REQUIRES_CHANGES（语义版未达 ≥ 关键词版）", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
