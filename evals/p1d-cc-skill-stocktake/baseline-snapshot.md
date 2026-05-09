# P1d v3 §1 Baseline Snapshot

执行时间:2026-05-07 11:00(local)
执行人:Claude Code(per plan v3 §责任分工 #1)
plan:`/Users/praise/.claude/plans/joyful-squishing-moonbeam.md`(v3 ready-for-implementation)

---

## §1.1 evidence pack 目录结构

```
Codex/ai-infra/akb2-toolbox-evals/p1d-cc-skill-stocktake/
├── fixtures/
│   └── skills/{skill-a, skill-b, skill-c}/   # 待 §2 写 SKILL.md
├── outputs/
│   ├── 01-scan/
│   ├── 02-quickdiff-nochange/
│   ├── 03-quickdiff-changed/
│   ├── 04-save-results/
│   ├── 05-evaluator/
│   └── 06-isolation-failure/
└── baseline-snapshot.md (本文件)
```

## §1.2 jq / bash 依赖

| 检查 | 结果 |
|------|------|
| `which jq` | `/usr/bin/jq` jq-1.7.1-apple ✓ |
| bash version | GNU bash 3.2.57(1) (arm64-apple-darwin25) ✓ |

## §1.3 bundled scripts baseline sha256(C-5 hard check #10 守门)

| script | sha256 | size |
|--------|--------|------|
| `scan.sh` | `b1f16b586e7e2f4c6eeb54ea3895cd5501e64a1e12e242c820193c1193d0b263` | 5677b |
| `quick-diff.sh` | `e8b48f77d9e34d0c8043f8fede01387947ecfe0e960f7bd04cce9a44e606c0e8` | 3271b |
| `save-results.sh` | `250968532dc3bf1074f024a710d5d8aeec930e6798269703072f062fad1905bd` | 1952b |

**承诺**:全 P1d 流程**只读**调用,§7 收尾时实测 sha256 必须 byte-identical 到上述 baseline。

## §1.4 5 SKILL.md baseline sha256

| slug | 实测 sha256 | 期望 baseline | 对账 | size |
|------|-------------|---------------|------|------|
| cc-skill-stocktake | `52d10db4618d540915c5e5e6053dcce51c4ca98d4285c829d88b9d832f9c8fb6` | 同 | ✅ byte-identical | 2120b |
| cc-executable-plan | `5af73fdadd91097cc60f8e6c3622427aeaac8382bda0d1525debe564f2c46d0a` | 同 | ✅ byte-identical | 3657b |
| cc-review | `592cf3ed4b2c6e3f96c63a500bdbe3bebbc375a19983debbfa7f1877f406d274` | 同 | ✅ byte-identical | 4953b |
| cc-akb-search | `b669380de8a0cd4319f0c978f4cfa69d2757b004a8569fc3adac8f633637f982` | 同 | ✅ byte-identical | 1742b |
| cc-local-llm | `3a31ebe4f5938864e752ea51d35d89403e62d31a352ac3eaf01a784721c73050` | 同 | ✅ byte-identical | 4425b |

**对账结论**:5/5 byte-identical(C-5 hard check #4 当前已守住)。

## §1.5 cc-skill-stocktake 5 件套 baseline sha256

| path | sha256(前 12)| size |
|------|--------------|------|
| `toolbox/interfaces/cc-skill-stocktake.md` | `c78650b047c6` | 2766b |
| `toolbox/tools/cc-skill-stocktake.md` | `e7bc54a7eaae` | 1902b |
| `toolbox/registry/cc-skill-stocktake.md` | `f35f67603f22` | 1475b |
| `toolbox/evals/cc-skill-stocktake.md` | `9c45f248debd` | 2723b |
| `wiki/tools/cc-skill-stocktake.md` | `a30fc7523dc0` | 3804b |

## §1.6 真实 stocktake cache baseline

| path | 状态 |
|------|------|
| `~/.claude/skills/skill-stocktake/results.json` | **不存在** — 本 P1d 0 写,无须 baseline 跟踪 |

## §1.7 wiki 待建链接 + ../concepts 实测

```
70:- [[../concepts/harness-engineering|Harness Engineering]] — skill 治理是 HE 7 大组件...(已建概念,非 (待建))
71:- [[../concepts/skill-lifecycle|Skill lifecycle]](待建)— 计划入库的 skill 状态机
72:- [[../concepts/freshness-tracking|Freshness tracking]](待建)— 计划入库的"内容陈旧追踪"通用概念
```

**Finding**:wiki 实测含 3 处 `../concepts/` 链接:
- line 70:`harness-engineering`(**已建**,非待建,**不应清理**)
- line 71:`skill-lifecycle`(**待建**,§2 清理)
- line 72:`freshness-tracking`(**待建**,§2 清理)

**plan §grep.2 patch 必要**:plan v3 §测试计划 §grep.2 写 `rg -n "\.\./concepts" "$AKB/wiki/tools/cc-skill-stocktake.md"  # 期望 空` — 这会因 line 70(已建概念)命中而 fail。

**正确 §grep.2 应为**:**只验 (待建) 后缀的 ../concepts 链接 0 命中**(line 71-72 待清);line 70 已建概念链接保留(不应误清)。

→ §2 实施时:patch plan §grep.2 + §grep.1 重新定义 + 仅清 line 71-72,line 70 保留。

## §1.8 global automation_allowed: true 计数

| 范围 | 命中 |
|------|------|
| `wiki/tools/` + `toolbox/` | **0** ✅ (P0 守门保持) |

## §1 总结

| 维度 | 状态 |
|------|------|
| jq / bash 依赖 | ✅ |
| 5 SKILL.md baseline 对账 | ✅ 5/5 byte-identical |
| 5 件套 baseline 记录 | ✅ |
| bundled scripts baseline 记录(3 script + sha256)| ✅(C-5 hard check #10 守门 baseline) |
| 真实 results.json | 不存在(0 风险) |
| wiki (待建) 残留 | 2 处(line 71-72,§2 清)|
| wiki ../concepts 已建链接 | 1 处(line 70 harness-engineering,**保留**)|
| P0 automation_allowed: true 守门 | ✅ 0 命中 |
| **plan §grep.2 patch 必要** | **是**(只验 (待建) 后缀,不验所有 ../concepts)|

## 下一步

§2 P1d-0 文档 + backlink + wiki cleanup:
1. **patch plan v3 §grep.2 + §grep.1**(in-scope plan range patch:仅清 (待建) 后缀的 ../concepts,line 70 已建概念保留)
2. Edit `~/.codex/skills/cc-skill-stocktake/SKILL.md` prepend 2 anchor 注释
3. 5 件套 docs 更新(暂保 status: candidate,留 §7 升 active)
4. wiki line 71-72 清理(line 70 保留)
5. Write P1d phase report
