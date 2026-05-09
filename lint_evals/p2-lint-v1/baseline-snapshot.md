---
phase: p2-toolbox-structure-lint-v0
step: §1-baseline-freeze
captured_at: 2026-05-07T12:15:00Z
plan_version: v1.1
plan_review_verdict: APPROVED
---

# P2 Toolbox Structure Lint v0 — §1 Baseline Snapshot

## 1. 依赖检查

| 工具 | 路径 | 版本 | 状态 |
|------|------|------|------|
| bash | /bin/bash | GNU bash 3.2.57(1) arm64 | ✓ |
| jq | /usr/bin/jq | jq-1.7.1-apple | ✓ |
| shasum | /usr/bin/shasum | 6.02 | ✓ |
| find | bfs 4.1 (via cc alias) | - | ✓ |
| grep | ugrep 7.5.0 (via cc alias) | - | ✓ |

注：find/grep 被 Claude Code 包装为 bfs/ugrep；runner 中使用 `command find` / `command grep` 绕过 alias。

## 2. 5 SKILL.md sha256 Baseline

| slug | sha256 (full) | 状态 |
|------|---------------|------|
| cc-executable-plan | `5af73fdadd91097cc60f8e6c3622427aeaac8382bda0d1525debe564f2c46d0a` | active L2 |
| cc-review | `592cf3ed4b2c6e3f96c63a500bdbe3bebbc375a19983debbfa7f1877f406d274` | active L2 |
| cc-local-llm | `3a31ebe4f5938864e752ea51d35d89403e62d31a352ac3eaf01a784721c73050` | active L1 |
| cc-skill-stocktake | `df16be83df67a04605b61294a210f9ad214b22e55ce40a45f34068dbaa2c48f2` | active L1 |
| cc-akb-search | `b669380de8a0cd4319f0c978f4cfa69d2757b004a8569fc3adac8f633637f982` | blocked candidate L0 |

✓ 与 plan §当前状态 baseline 一致。

## 3. 25 文件 sha256 Baseline（5 工具 × 5 件套）

### cc-executable-plan

| 件套 | sha256 |
|------|--------|
| toolbox/interfaces/cc-executable-plan.md | `4abf64341c4bd261d6574378cd709e1a2634b0c19ce716c4d261a1ab3dc723ca` |
| toolbox/tools/cc-executable-plan.md | `c56cdb3d697e8f769cfd90dabbcaa2741f84f4fc5d1c7941196bdb88a0107a8b` |
| toolbox/registry/cc-executable-plan.md | `02fcd0ed79c22e547aac70aa312a746a43ee340e13d36cb2863735a5d5905ccb` |
| toolbox/evals/cc-executable-plan.md | `c3f295e3f52f726afe100f0ddd31f2efdd1cae8c0fab882227366507f8b33fca` |
| wiki/tools/cc-executable-plan.md | `ac81fa8560f463ea2667a0b7c7ce84184947857679088ac85e6c3aa13068b2dc` |

### cc-review

| 件套 | sha256 |
|------|--------|
| toolbox/interfaces/cc-review.md | `a59a715a6e963b67c8dc6618c2702be4e93a7e1c00d92236b4a6d3af88ae62ca` |
| toolbox/tools/cc-review.md | `9defbad458b49e8340024a4dce7ca72fc03d619bf6039c3861dab45c0882d129` |
| toolbox/registry/cc-review.md | `7385d7df55a0512d3a2145d124950d78a0e562f5dd4ea11f916da7dbb5cae024` |
| toolbox/evals/cc-review.md | `941268b377e62d8c04015686738b272ae6e23ecc4b8c55227e8a0837d9ca4624` |
| wiki/tools/cc-review.md | `00db7eb42b6ad765ef970e9f4c169a64accb009e222e1fc8c5c5a49884e9cd3e` |

### cc-local-llm

| 件套 | sha256 |
|------|--------|
| toolbox/interfaces/cc-local-llm.md | `941ffed988d6234e7d1e18d5f804367402024d3376115c1c22fb9232d832211f` |
| toolbox/tools/cc-local-llm.md | `6dea0a7abec61140c07d77a1683ab067cc180a458f041da9bccd85368de69bdf` |
| toolbox/registry/cc-local-llm.md | `69b08b16ec0e85dd89c3f20b37b87025e0100e1de025d506579816d988178374` |
| toolbox/evals/cc-local-llm.md | `5271b906a94860cf899bd4bd7c8be09134ffba5018658633aac0875bfa93b587` |
| wiki/tools/cc-local-llm.md | `1e9f8dad4c41a7d18dac6a51d4966c6dd90b75d3977bcb077fc1b96015395cb5` |

### cc-skill-stocktake

| 件套 | sha256 |
|------|--------|
| toolbox/interfaces/cc-skill-stocktake.md | `5ea0fac2c095ec69d4272ee6a92f3c747d4e2052cdb5b07c7692fc3a90427192` |
| toolbox/tools/cc-skill-stocktake.md | `00789a38e59902c3bea036ba5a2ad46de3d43f210f62f00c5d5b098b5691e1c7` |
| toolbox/registry/cc-skill-stocktake.md | `c4b14ea17c3c69e66d858dddd5866c58586735233eb74d937d885ea98add0cd7` |
| toolbox/evals/cc-skill-stocktake.md | `9661931eb0d93978c01260bda2cd4ff70c1a95f4e4be3c228d250dc9e652a70a` |
| wiki/tools/cc-skill-stocktake.md | `8429d4ce79701cdd45fb088854b444337d7cc01c4cd6df09d844a9b25c2cdbce` |

### cc-akb-search（blocked candidate）

| 件套 | sha256 |
|------|--------|
| toolbox/interfaces/cc-akb-search.md | `4539b8a4e960b415959716277406f3ee9ff9a766b3403fa020d9f6175cc17404` |
| toolbox/tools/cc-akb-search.md | `dc05bfc7fee29aabda18d75316792c9d895a3a48e616155c6f3bea4e12dd1c20` |
| toolbox/registry/cc-akb-search.md | `291e301273021b1249f34327e5d6cdf01fba2884c8175b92f553ed8994fb89fd` |
| toolbox/evals/cc-akb-search.md | `3b405acde76ca84a4302aee1224906d46936dae2cec2e15bdfbf16e65aae493c` |
| wiki/tools/cc-akb-search.md | `6e04bcd0825f66a2dc8c34cea80c9191015a82b6b753e3afffef25ff98c9d4f2` |

✓ 全部 25 文件存在（无 MISSING）。

## 4. Bundled Scripts Baseline（cc-skill-stocktake）

路径：`~/.codex/skills/cc-skill-stocktake/references/source-bundled-resources/scripts/`

| 脚本 | sha256 |
|------|--------|
| scan.sh | `b1f16b586e7e2f4c6eeb54ea3895cd5501e64a1e12e242c820193c1193d0b263` |
| quick-diff.sh | `e8b48f77d9e34d0c8043f8fede01387947ecfe0e960f7bd04cce9a44e606c0e8` |
| save-results.sh | `250968532dc3bf1074f024a710d5d8aeec930e6798269703072f062fad1905bd` |

注：lint v0 **不依赖** bundled scripts（结构 lint，不是工具行为 lint）。Baseline 仅作 C-5 hard check #8 参考。

## 5. 全局 automation_allowed: true 守门

```
rg '^automation_allowed: true$' AKB2.0/toolbox AKB2.0/wiki/tools → count = 0
```

✓ P0 守门通过（0 个 automation_allowed: true）。

## 6. 证据目录结构确认

```
Codex/ai-infra/akb2-toolbox-lint/p2-lint-v0/
├── baseline-snapshot.md    ← 本文件
├── fixtures/               ✓ 已建
├── outputs/                ✓ 已建
└── rules/                  ✓ 已建
```

## 7. §1 总结

- 25 文件 sha256 全采集 ✓（P3 residual 已解决）
- 5 SKILL.md sha256 全采集且与 plan baseline 一致 ✓
- bundled scripts sha256 全采集 ✓
- 依赖 jq/bash/shasum/find/grep 全就位 ✓
- global automation_allowed: true = 0 ✓
- 证据目录结构就位 ✓
- plan v1.1 APPROVED ✓

§1 complete → 进入 §2。
