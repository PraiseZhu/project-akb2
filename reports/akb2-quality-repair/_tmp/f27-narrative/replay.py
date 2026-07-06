#!/usr/bin/env python3
"""F27 机制叙述编织 — 两篇真机复放(改前/改后通用)

生产默认配置(AUTO_PROMOTE=0,不设行为开关)+ 真实 LLM + raw 只读 + 隔离 staging,
对两篇 raw(addy Long-running / New SW Lifecycle)跑 extract→compose→grade(2 次取较差),
报机制叙述节文本 + grader defects 数 + 最终结局。

改前(origin/main):机制叙述仅一句/组件不进叙述/anchor 缩写/TLDR 噪音
改后(worktree):eligible 边+带证据组件织叙述/anchor 全称/TLDR 清洗

用法: python3 replay.py [fixed|baseline]
"""
from __future__ import annotations
import json, os, re, sys, time, traceback
from pathlib import Path

os.environ.setdefault("AKB2_AUTO_PROMOTE", "0")
REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

from src.akb2.quality.distill import compose_candidate, extract_claims  # noqa: E402
from src.akb2.quality.grader import run_grader_detailed  # noqa: E402

TARGETS = [
    ("addy-long-running", "concept",
     "/Volumes/AKB2/Obsidian/Praise Space/AKB2.0/raw/addy-osmani--de4f29655cc7--raw--2026-07-05.md"),
    ("new-sw-lifecycle", "concept",
     "/Volumes/AKB2/Obsidian/Praise Space/AKB2.0/raw/addy-osmani--6db52aeb5bef--raw--2026-07-05.md"),
]


def _extract_section(text: str, section: str) -> str:
    """从 candidate 文本提取指定节(## 标题到下一个 ## )。"""
    m = re.search(rf"{re.escape(section)}.*?\n(.*?)(?:\n## |\Z)", text, re.S)
    return m.group(1).strip()[:800] if m else "(节不存在)"


def run_one(name: str, lane: str, raw_path: str) -> dict:
    print(f"\n{'='*60}\n[{name}] lane={lane}\n{'='*60}", flush=True)
    rec: dict = {"name": name, "raw_path": raw_path}
    if not Path(raw_path).exists():
        rec["error"] = f"raw 不存在"
        return rec
    raw = Path(raw_path).read_text(encoding="utf-8")
    staging = REPO / "reports/akb2-quality-repair/_tmp/f27-narrative/staging"
    staging.mkdir(parents=True, exist_ok=True)
    # 1. extract
    t0 = time.time()
    try:
        result = extract_claims(raw, route_llm_fn=None, content_lane=lane)
        rec["extract_success"] = result.success
        rec["extract_retries"] = result.retries_used
        rec["extract_elapsed"] = round(time.time() - t0, 1)
        if not result.success:
            rec["extract_error"] = (result.error or "")[:200]
            print(f"  extract FAIL: {rec['extract_error']}", flush=True)
            return rec
        data = result.data or {}
        comps = data.get("mechanism_components", []) or []
        edges = data.get("causal_edges", []) or []
        rec["component_count"] = len(comps)
        rec["edge_count"] = len(edges)
        print(f"  extract OK: components={len(comps)} edges={len(edges)} elapsed={rec['extract_elapsed']}s", flush=True)
    except Exception as exc:
        rec["extract_exception"] = f"{type(exc).__name__}: {exc}"
        print(f"  extract EXC: {rec['extract_exception']}", flush=True)
        return rec
    # 2. compose
    t1 = time.time()
    try:
        comp = compose_candidate(
            result, raw_path, f"f27-{name}", output_dir=str(staging),
            raw_content=raw, content_lane=lane,
        )
        rec["compose_success"] = comp.success
        if not comp.success:
            rec["compose_error"] = (comp.error or "")[:200]
            print(f"  compose FAIL: {rec['compose_error']}", flush=True)
            return rec
        cand_path = comp.candidate_path
        cand = Path(cand_path).read_text(encoding="utf-8")
        rec["compose_elapsed"] = round(time.time() - t1, 1)
        # 机制叙述节
        narrative = _extract_section(cand, "机制叙述")
        rec["mechanism_narrative"] = narrative
        print(f"\n  --- 机制叙述节 ---\n{narrative[:500]}\n", flush=True)
        # 机制组件与因果边证据节(anchor 归一验证)
        mech_section = _extract_section(cand, "机制组件与因果边证据")
        rec["mechanism_section"] = mech_section[:400]
        # TLDR 节(噪音清洗验证)
        tldr = _extract_section(cand, "TLDR")
        rec["tldr"] = tldr[:300]
        has_noise = bool(re.search(r"\d+;\s*\d+", tldr))
        rec["tldr_has_noise"] = has_noise
        print(f"  TLDR noise(分号数字对): {has_noise}", flush=True)
    except Exception as exc:
        rec["compose_exception"] = f"{type(exc).__name__}: {exc}"
        print(f"  compose EXC: {rec['compose_exception']}", flush=True)
        traceback.print_exc()
        return rec
    # 3. grade 2 次(取较差)
    grade_results = []
    for i in range(2):
        t2 = time.time()
        try:
            g = run_grader_detailed(cand, content_lane=lane)
            dt = time.time() - t2
            grade_results.append({
                "defects": len(g.defects),
                "model": g.model_used,
                "primary_failed": g.primary_failed,
                "fallback_used": g.fallback_used,
                "suspended": g.suspended,
                "elapsed": round(dt, 1),
            })
            print(f"  grade[{i}]: defects={len(g.defects)} model={g.model_used} elapsed={dt:.1f}s", flush=True)
        except Exception as exc:
            grade_results.append({"exception": f"{type(exc).__name__}: {exc}"[:150]})
            print(f"  grade[{i}] EXC: {exc}", flush=True)
    rec["grade_results"] = grade_results
    # 取较差(defects 最多那次)
    valid = [g for g in grade_results if "defects" in g]
    if valid:
        worst = max(valid, key=lambda g: g["defects"])
        rec["grade_worst_defects"] = worst["defects"]
        rec["grade_worst_model"] = worst.get("model", "")
        rec["final_suspended"] = any(g.get("suspended") for g in valid)
        print(f"  grade worst: defects={worst['defects']} suspended={rec['final_suspended']}", flush=True)
    return rec


def main() -> None:
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    out_dir = REPO / "reports/akb2-quality-repair/_tmp/f27-narrative"
    out_dir.mkdir(parents=True, exist_ok=True)
    records = [run_one(n, l, p) for n, l, p in TARGETS]
    out_file = out_dir / f"replay-{label}.json"
    out_file.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[written] {out_file}", flush=True)
    print(f"\n=== SUMMARY ({label}) ===", flush=True)
    for r in records:
        if r.get("extract_success"):
            narr = r.get("mechanism_narrative", "")
            has_narr = "机制叙述" in narr or len(narr) > 50
            print(f"  {r['name']}: comps={r.get('component_count')} edges={r.get('edge_count')} "
                  f"narrative_len={len(narr)} grade_worst={r.get('grade_worst_defects','?')} "
                  f"suspended={r.get('final_suspended','?')}", flush=True)
        else:
            print(f"  {r['name']}: extract FAIL {r.get('extract_error','')[:80]}", flush=True)


if __name__ == "__main__":
    main()
