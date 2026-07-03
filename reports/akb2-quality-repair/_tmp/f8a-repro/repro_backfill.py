"""F8-A 离线复现:直接调 _backfill_component_evidence,看 ag2 4 组件 backfilled 数。

模拟 LLM 产 4 组件(probe-p6 ag2 真实组件名),evidence 全空。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path("/Users/praise/AI-Agent/Claude/projects/_akb2-quality-repair-v2-wt")
sys.path.insert(0, str(ROOT / "src"))

from akb2.quality.distill import (  # noqa: E402
    _backfill_component_evidence,
    _component_match_variants,
    _compile_component_match_pattern,
    _generate_component_variants,
)
from akb2.quality.raw_anchors import declared_anchor_set  # noqa: E402

RAW_PATH = ROOT / "reports/akb2-quality-repair/_tmp/raw-store/2fb4f618c181edf17f8ebbf7f51ef41b1fe4bb73c3525eb857aeb68832359894.md"
raw_content = RAW_PATH.read_text()

# 模拟 LLM 产 4 组件(probe-p6 ag2 真实组件名),evidence 全空
components = [
    {"name": "Captain", "role": "coordinator", "evidence_anchor": "", "evidence_sentence": ""},
    {"name": "Executor Pool", "role": "execution", "evidence_anchor": "", "evidence_sentence": ""},
    {"name": "Task Graph and Checkpointing", "role": "state", "evidence_anchor": "", "evidence_sentence": ""},
    {"name": "Sub-Agent Lifecycle", "role": "lifecycle", "evidence_anchor": "", "evidence_sentence": ""},
]
data = {"mechanism_components": components, "claims": [], "causal_edges": [], "how_steps": []}

print("=== 修前:backfill 前 ===")
for c in components:
    print(f"  {c['name']!r:35} ea={c['evidence_anchor']!r} es={c['evidence_sentence'][:40]!r}")

# 诊断:每个组件的 variants + pattern
print("\n=== variants 诊断 ===")
for c in components:
    v = _generate_component_variants(c["name"])
    vm = _component_match_variants([{"name": c["name"], "role": "x"}])
    pat = _compile_component_match_pattern(vm)
    print(f"  {c['name']!r:35} variants={v}")
    print(f"  {'':35} variant_map={vm} pattern={'有' if pat else '无'}")

backfilled = _backfill_component_evidence(data, raw_content)
print(f"\n=== backfilled count = {backfilled} ===")

print("\n=== 修前:backfill 后 ===")
for c in components:
    print(f"  {c['name']!r:35}")
    print(f"    ea={c['evidence_anchor']!r}")
    print(f"    es={c['evidence_sentence'][:80]!r}")

# 锚点声明集
decl = declared_anchor_set(raw_content)
print(f"\n=== raw 声明锚({len(decl)}个) ===")
print(f"  {sorted(decl)}")

# 校验:回填的锚是否∈声明集
print("\n=== 校验:回填锚∈声明集? ===")
for c in components:
    ea = c["evidence_anchor"]
    ok = ea in decl if ea else "空"
    print(f"  {c['name']!r:35} ea={ea!r} ∈decl={ok}")
