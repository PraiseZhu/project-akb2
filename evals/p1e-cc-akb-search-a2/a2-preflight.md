# A2 Preflight — cc-akb-search Unblock A2 配置授权计划

```yaml
phase: p1e-a2-config-authorization
timestamp: 2026-05-08T00:00:00Z
plan: cc-akb-search A2 配置授权计划 v1.0
scope: read-only-draft-only
mutations: none
predecessor_phase: p1e-a1-readonly-discovery
predecessor_verdict: not_mounted
```

## A1 Verdict 引用

| 字段 | 值 |
|------|-----|
| verdict | not_mounted |
| mcp_entry_visible | false |
| skill_sha256_drift | false |
| next_step | a2_config_authorization_plan |
| consistency_with_c4b | true |

## SKILL.md sha256 Baseline

```bash
shasum -a 256 ~/.codex/skills/cc-akb-search/SKILL.md
# A2 时实测: b669380de8a0cd4319f0c978f4cfa69d2757b004a8569fc3adac8f633637f982
```

| check | result |
|-------|--------|
| sha256 matches A1 baseline (b669380de8a0...) | ✓ PASS |
| sha256 matches C-4b baseline | ✓ PASS（byte-identical across 3 days） |

## Config Files Baseline（A3 Rollback Reference）

| 文件 | sha256 | servers | akb-search |
|------|--------|---------|-----------|
| `~/.claude/mcp-configs/mcp-servers.json` | `04cb0509472fc0b7e3a36bd52f2a5b6a0d92a9d9160cd5c76a86dd7a95ca0406` | 27 | ✗ absent |
| `iCloud/Claude Code/mcp-configs/mcp-servers.json` | `04cb0509472fc0b7e3a36bd52f2a5b6a0d92a9d9160cd5c76a86dd7a95ca0406` | 27 | ✗ absent |

两文件 sha256 byte-identical。servers_count = 27（A1 报告时为 26，中间新增 1 个 server）。

## Five-File-Set Status

| 文件 | status | eval_level | automation_allowed | risk_level | preflight_status |
|------|--------|-----------|-------------------|------------|-----------------|
| toolbox/interfaces | candidate | — | false | high | — |
| toolbox/tools | candidate | — | false | high | — |
| toolbox/registry | candidate | — | false | high | — |
| toolbox/evals | candidate | L0 | false | — | blocked |
| wiki/tools | **active** | — | false | high | — |

> wiki/tools status=active = understanding page，toolbox_status = candidate 不变。

## A2 Scope Check

| constraint | verified |
|-----------|----------|
| mcp-configs 未修改 | ✓ |
| settings.json 未修改 | ✓ |
| SKILL.md 未修改 | ✓ |
| 五件套文件未修改 | ✓ |
| automation_allowed 全 false | ✓（5/5） |
| wrapper-draft.py 仅 draft，未 deploy | ✓ |
| mcp-entry-draft.json 仅 draft，未写入配置 | ✓ |

## A2 Deliverable Check

| deliverable | status |
|-------------|--------|
| a2-config-baseline.json | ✓ created |
| wrapper-draft.py | ✓ created (draft copy in evidence pack; canonical draft at Codex/plugins/.../server.py sha256=0ef83866...（F1 修复后）) |
| mcp-entry-draft.json | ✓ created (draft) |
| a2-verification-plan.md | ✓ created |
| a2-preflight.md | ✓ (this file) |
| run-evidence.md | pending |
