#!/usr/bin/env python3
"""F18 grader schema 修复 — 复放脚本(改前/改后通用)

生产默认配置(不设任何行为开关、AUTO_PROMOTE=0)下,对账本死链条目做隔离复放,
报 grader 行为:primary 是否 400/错 keys、fallback_used、最终结局。
真实 LLM 调用,不 mock。只读 candidate,不写 vault 正库。

用法:
  python3 replay.py [baseline|fixed] [target_index]
  target_index: 0=openai-blog--3d99(concept), 1=addyosmani(procedural), 2=fixture-ag2(procedural)
  不传 index 跑全量 3 条。
"""
from __future__ import annotations
import json, os, sys, time, traceback
from pathlib import Path

# 不设任何行为开关(AKB2_DISTILL_SEMANTIC_RETAKE 等一律不碰)
os.environ.setdefault("AKB2_AUTO_PROMOTE", "0")

REPO = Path("/Users/praise/AI-Agent/Claude/projects/_akb2-grader-schema-wt")
# grader.py 用 `from src.akb2...` 导入,需 REPO 在 sys.path; toolbox_scan 需 REPO/src
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

from src.akb2.quality.grader import run_grader_detailed  # noqa: E402

TARGETS = [
    ("openai-blog--3d99", "concept",
     "/Volumes/AKB2/Obsidian/Praise Space/AKB2.0/staging/candidate/2026-06-29-154938/openai-blog--3d99cf87d464.md"),
    ("addyosmani-loop-eng", "",
     "/Volumes/AKB2/Obsidian/Praise Space/AKB2.0/staging/candidate/2026-06-26-170907/addyosmani.com-blog-loop-engineering-.md"),
    ("fixture-ag2-captain", "",
     "/Volumes/AKB2/Obsidian/Praise Space/AKB2.0/staging/candidate/2026-06-26-170519/fixture:ag2-captain-agent-concept.md"),
]


def run_one(name: str, lane: str, path: str) -> dict:
    print(f"\n{'='*60}\n[{name}] lane={lane or 'procedural(default)'}\n{'='*60}", flush=True)
    text = Path(path).read_text(encoding="utf-8")
    t0 = time.time()
    rec: dict = {"name": name, "content_lane": lane or "procedural", "path": path}
    try:
        result = run_grader_detailed(text, content_lane=lane)
        dt = time.time() - t0
        rec.update({
            "model_used": result.model_used,
            "primary_failed": result.primary_failed,
            "primary_failure_reason": result.primary_failure_reason[:300],
            "fallback_used": result.fallback_used,
            "fallback_failed": result.fallback_failed,
            "fallback_failure_reason": result.fallback_failure_reason[:300],
            "suspended": result.suspended,
            "defects_count": len(result.defects),
            "interpretation_entailment": result.interpretation_entailment,
            "weighted_total": result.weighted_total,
            "elapsed_s": round(dt, 1),
        })
        print(f"  model_used={result.model_used}", flush=True)
        print(f"  primary_failed={result.primary_failed}", flush=True)
        if result.primary_failure_reason:
            print(f"  primary_reason={result.primary_failure_reason[:200]}", flush=True)
        print(f"  fallback_used={result.fallback_used} fallback_failed={result.fallback_failed}", flush=True)
        print(f"  suspended={result.suspended} defects={len(result.defects)}", flush=True)
        print(f"  entailment={result.interpretation_entailment} weighted={result.weighted_total}", flush=True)
        print(f"  elapsed={dt:.1f}s", flush=True)
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["traceback"] = traceback.format_exc()[:800]
        print(f"  EXCEPTION: {rec['error']}", flush=True)
    return rec


def main() -> None:
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    idx = int(sys.argv[2]) if len(sys.argv) > 2 else None
    out_dir = REPO / "reports/akb2-quality-repair/_tmp/f18-grader-schema"
    out_dir.mkdir(parents=True, exist_ok=True)
    targets = TARGETS if idx is None else [TARGETS[idx]]
    records = [run_one(n, l, p) for n, l, p in targets]
    out_file = out_dir / f"replay-{label}.json"
    out_file.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[written] {out_file}", flush=True)
    # 摘要
    print(f"\n=== SUMMARY ({label}) ===", flush=True)
    for r in records:
        ok = (not r.get("primary_failed")) and (not r.get("suspended"))
        flag = "OK" if ok else "FAIL"
        print(f"  [{flag}] {r['name']}: model={r.get('model_used')} pf={r.get('primary_failed')} fu={r.get('fallback_used')} susp={r.get('suspended')}", flush=True)


if __name__ == "__main__":
    main()
