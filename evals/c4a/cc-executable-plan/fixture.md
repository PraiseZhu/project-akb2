# Fixture — cc-executable-plan L1 smoke

> Codex must invoke the `cc-executable-plan` skill with the input below and
> capture the full skill output into `run-evidence.md` in the same dir.

## Skill metadata at smoke time

- skill path: `~/.codex/skills/cc-executable-plan/SKILL.md`
- expected sha256: `69364def87afa9ad88e66bf1b2285d0007ee245070b4ed8a6367496dc50357e8`
- workspace_owner: `official-codex-skills`

## Pre-call checks (Codex)

1. `shasum -a 256 ~/.codex/skills/cc-executable-plan/SKILL.md` matches expected sha256
2. Template drift check passes:

```bash
diff -q "/Users/praise/.claude/templates/plan-template.md" \
        "/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/templates/plan-template.md"
```

   If drift, abort and write blocked evidence; do NOT generate a plan.

## Input

**Goal**: 把 ECC inventory 工具中已弃用的 `legacy-skill-importer.sh` 标记为
`deprecated` 并提供替代脚本 `skill-importer-v2.sh` 的迁移路径。期望产出一个
最小可执行计划。

**Context**: 已存在两个文件:
- `Codex/ai-infra/inventory/legacy-skill-importer.sh`(2024-Q1 写的,330 行)
- `Codex/ai-infra/inventory/skill-importer-v2.sh`(2025-Q1 重写,150 行,已实测)

迁移目标:删除老脚本前必须先把所有调用方迁移到 v2,且保留至少 7 天回滚窗口。

**Expected behavior**: skill 应输出符合
`~/.claude/templates/plan-template.md` 模板的中文 plan,包含全部 11 节标题。

## What to capture in run-evidence.md

1. timestamp
2. exact command / invocation Codex used
3. full skill output (raw markdown)
4. sha256 of the produced plan output (so it's reproducible)
5. drift check result (pass/fail with diff output if any)
