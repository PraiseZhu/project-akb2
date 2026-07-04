#!/usr/bin/env python3
"""F19 fallback/compose 渲染缺陷 — deterministic 链路复放脚本(改前/改后通用)

对每条 raw 跑 deterministic 链路(scan must_cover + fallback extract + compose _build_body
+ SG run_candidate_self_gate),报 must_cover/占位/fallback 缺陷。
不调 LLM(_build_body 是确定性脚本),验证根因 1(归一化)+2(占位)+3(fallback)端到端。

code.claude.com sub-agents raw 不在本机(corpus.yaml 引用 URL 但 raw 未落地),
用 c234(根因3)+ addyosmani(根因1,raw 在)+ ag2-captain fixture(distill_no_converge)替代。

用法: python3 replay.py [fixed|baseline]
"""
from __future__ import annotations
import json, os, sys, traceback
from pathlib import Path

os.environ.setdefault("AKB2_AUTO_PROMOTE", "0")
REPO = Path(__file__).resolve().parents[4]  # reports/akb2-quality-repair/_tmp/f19-fallback-render/ → repo root
sys.path.insert(0, str(REPO))  # for `from src.akb2...`
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "src/akb2/toolbox_scan"))

from src.akb2.quality.distill import _build_deterministic_extract_data, _build_body  # noqa: E402
from src.akb2.quality.checks_structural import _scan_source_evidence, run_candidate_self_gate  # noqa: E402

TARGETS = [
    ("c234-langchain", "",
     "/Volumes/AKB2/Obsidian/Praise Space/AKB2.0/raw/langchain-blog--c234f0aa9f55--raw--2026-06-29.md"),
    ("addyosmani-loop-eng", "",
     "/Volumes/AKB2/Obsidian/Praise Space/AKB2.0/raw/addy-osmani-loop-engineering--raw--2026-06-11.md"),
    ("ag2-captain-fixture", "concept",
     "/Users/praise/AI-Agent/Claude/projects/Project AKB2/evals/main-pipeline-quality/fixtures/ag2-captain-agent-concept.md"),
]


def _is_vacuous_op(literal: str) -> bool:
    s = literal.strip()
    return (not s) or s.isdigit() or len(s) < 3


def run_one(name: str, lane: str, raw_path: str) -> dict:
    print(f"\n{'='*60}\n[{name}] lane={lane or 'procedural'}\n{'='*60}", flush=True)
    rec: dict = {"name": name, "content_lane": lane or "procedural", "raw_path": raw_path}
    if not Path(raw_path).exists():
        rec["error"] = f"raw 不存在: {raw_path}"
        print(f"  SKIP: {rec['error']}", flush=True)
        return rec
    raw = Path(raw_path).read_text(encoding="utf-8")
    # 根因 2:scan must_cover(占位过滤)
    items = _scan_source_evidence(raw)
    ops = [it for it in items if it.kind == "source_operation"]
    vacuous_ops = [it for it in ops if _is_vacuous_op(it.literal.replace("Step ", "").split(":")[-1].strip() if "Step" in it.literal else it.literal.split(". ", 1)[-1] if ". " in it.literal else it.literal)]
    rec["must_cover_items"] = len(items)
    rec["source_operation_items"] = len(ops)
    rec["vacuous_ops_before_filter"] = len(vacuous_ops)  # 应为 0(已过滤)
    print(f"  must_cover={len(items)} source_op={len(ops)} vacuous(残留)={len(vacuous_ops)}", flush=True)
    # 根因 3:fallback extract
    try:
        data = _build_deterministic_extract_data(raw, raw_path=raw_path, content_lane=lane)
        rec["fallback_claims"] = len(data["claims"])
        short_claims = [c for c in data["claims"] if len(c["text"]) < 20]
        rec["short_claims"] = len(short_claims)
        rec["fallback_ok"] = True
        print(f"  fallback claims={len(data['claims'])} short(<20)={len(short_claims)}", flush=True)
        for c in data["claims"][:3]:
            print(f"    claim: {c['text'][:70]!r}", flush=True)
    except ValueError as e:
        rec["fallback_ok"] = False
        rec["fallback_error"] = str(e)
        print(f"  fallback fail-closed: {e}", flush=True)
        return rec
    # compose _build_body
    try:
        body = _build_body(data, raw_path, None, must_cover=items, raw_content=raw, content_lane=lane)
        rec["body_len"] = len(body)
    except Exception as e:
        rec["compose_error"] = f"{type(e).__name__}: {e}"
        print(f"  compose ERR: {rec['compose_error']}", flush=True)
        return rec
    # SG(根因 1:归一化 must_cover 匹配)
    candidate = f"---\ncontent_lane: {lane or 'procedural'}\n---\n{body}"
    defects = run_candidate_self_gate(candidate, raw_content=raw)
    mc = [d for d in defects if "must_cover" in str(d).lower() or "SG-must" in str(d)]
    rec["sg_defects"] = len(defects)
    rec["must_cover_defects"] = len(mc)
    rec["must_cover_defect_details"] = [getattr(d, "defect", "")[:100] for d in mc]
    print(f"  SG defects={len(defects)} must_cover={len(mc)}", flush=True)
    for d in mc:
        print(f"    mc defect: {getattr(d,'defect','')[:100]}", flush=True)
    return rec


def main() -> None:
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    out_dir = REPO / "reports/akb2-quality-repair/_tmp/f19-fallback-render"
    out_dir.mkdir(parents=True, exist_ok=True)
    records = [run_one(n, l, p) for n, l, p in TARGETS]
    out_file = out_dir / f"replay-{label}.json"
    out_file.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[written] {out_file}", flush=True)
    print(f"\n=== SUMMARY ({label}) ===", flush=True)
    for r in records:
        if "error" in r and "raw" in r.get("error", ""):
            print(f"  [SKIP] {r['name']}: {r['error']}", flush=True)
            continue
        ok = r.get("fallback_ok") and r.get("must_cover_defects", 0) == 0
        flag = "OK" if ok else "FAIL"
        print(f"  [{flag}] {r['name']}: mc_items={r.get('must_cover_items')} claims={r.get('fallback_claims')} short={r.get('short_claims')} mc_defects={r.get('must_cover_defects')}", flush=True)


if __name__ == "__main__":
    main()
