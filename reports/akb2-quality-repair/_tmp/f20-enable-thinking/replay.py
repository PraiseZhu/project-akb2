#!/usr/bin/env python3
"""F20 enable_thinking 400 — 真机复验脚本(改前/改后通用)

设 AKB2_DISTILL_FAMILY=gpt-5.4(B 臂,与账本命中条目同配置——账本 model_family=gpt-5.4
证实生产 distill_extract 调 gpt-5.4)+ AUTO_PROMOTE=0 + 不设行为开关,跑 extract_claims
复现 enable_thinking 400。真实 LLM,不 mock。

改前(origin/main):route_llm 无差别透传 enable_thinking → gpt-5.4 400 → 3/3 烧光 → fallback
改后(worktree):route_llm 按模型族过滤 → gpt-5.4 不收 enable_thinking → 不 400 → 成功/正常 fail

用法: python3 replay.py [fixed|baseline]
"""
from __future__ import annotations
import json, os, sys, time, traceback
from pathlib import Path

# 复现账本 400:设 AKB2_DISTILL_FAMILY=gpt-5.4(B 臂,与账本命中条目同配置)
os.environ.setdefault("AKB2_DISTILL_FAMILY", "gpt-5.4")
os.environ.setdefault("AKB2_AUTO_PROMOTE", "0")

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "src/akb2/toolbox_scan"))

from src.akb2.quality.distill import extract_claims, DISTILL_MODEL_FAMILY, MAX_EXTRACT_RETRIES  # noqa: E402

TARGETS = [
    ("ag2-captain-fixture", "concept",
     "/Users/praise/AI-Agent/Claude/projects/Project AKB2/evals/main-pipeline-quality/fixtures/ag2-captain-agent-concept.md"),
    ("vercel-ai-sdk-fixture", "",
     "/Users/praise/AI-Agent/Claude/projects/Project AKB2/evals/main-pipeline-quality/fixtures/vercel-ai-sdk-nosource.md"),
]


def run_one(name: str, lane: str, raw_path: str) -> dict:
    print(f"\n{'='*60}\n[{name}] lane={lane or 'procedural'} DISTILL_MODEL_FAMILY={DISTILL_MODEL_FAMILY}\n{'='*60}", flush=True)
    rec: dict = {"name": name, "content_lane": lane or "procedural", "distill_family": DISTILL_MODEL_FAMILY}
    if not Path(raw_path).exists():
        rec["error"] = f"raw 不存在: {raw_path}"
        print(f"  SKIP: {rec['error']}", flush=True)
        return rec
    raw = Path(raw_path).read_text(encoding="utf-8")
    t0 = time.time()
    try:
        result = extract_claims(raw, route_llm_fn=None, content_lane=lane)
        dt = time.time() - t0
        rec.update({
            "success": result.success,
            "retries_used": result.retries_used,
            "error": (result.error or "")[:300],
            "elapsed_s": round(dt, 1),
        })
        print(f"  success={result.success} retries_used={result.retries_used} elapsed={dt:.1f}s", flush=True)
        if result.error:
            print(f"  error: {result.error[:250]}", flush=True)
        if result.success:
            data = result.data or {}
            print(f"  claims={len(data.get('claims',[]))} how_steps={len(data.get('how_steps',[]))}", flush=True)
    except Exception as exc:
        rec["exception"] = f"{type(exc).__name__}: {exc}"
        rec["traceback"] = traceback.format_exc()[:600]
        print(f"  EXCEPTION: {rec['exception']}", flush=True)
    return rec


def main() -> None:
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    out_dir = REPO / "reports/akb2-quality-repair/_tmp/f20-enable-thinking"
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
        ok = r.get("success")
        flag = "OK" if ok else "FAIL"
        has_400 = "400" in (r.get("error") or "")
        has_enable_thinking = "enable_thinking" in (r.get("error") or "")
        print(f"  [{flag}] {r['name']}: success={ok} retries={r.get('retries_used')} 400={has_400} enable_thinking_400={has_enable_thinking}", flush=True)


if __name__ == "__main__":
    main()
