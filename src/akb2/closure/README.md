# closure

阶段闭环 manifest 管理（源自 `Codex/ai-infra/akb2-toolbox-closure/`，已迁入）：

- `2026-05-07/closure-manifest.json` — primary baseline（SHA `0fbd5370…`，只读）
- `2026-05-08-p1e-delta/` — delta + effective + regression（cc-akb-search L1 升级）
- `merge-baselines.sh` — primary + delta → effective 派生（含 a1_mcp_discovery 透传 + pending_next_phases 重写 + exit 6 守卫）

详见 [`docs/runbooks/re-anchor-closure-manifest.md`](../../../docs/runbooks/re-anchor-closure-manifest.md)。
