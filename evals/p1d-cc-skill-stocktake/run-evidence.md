# P1d C-4 L1 Smoke Run Evidence — cc-skill-stocktake

## 元数据

| 字段 | 值 |
|------|------|
| Phase | P1d L1 smoke(C-4) |
| Tool | `cc-skill-stocktake` |
| SKILL.md sha256(P1d §2 后,C-4 全程不变)| `df16be83df67a04605b61294a210f9ad214b22e55ce40a45f34068dbaa2c48f2`(prepend 2 行 anchor;旧 baseline `52d10db4618d`) |
| Bundled scripts(byte-identical 守门 全程) | scan.sh `b1f16b586e7e` / quick-diff.sh `e8b48f77d9e3` / save-results.sh `250968532dc3` |
| **Execution mode** | **Claude 全自动**(Bash + jq + deterministic mock evaluator;0 LLM call;0 网络;0 localhost;0 user-handoff)|
| Sandbox workaround | mktemp PATH wrapper(`<evidence>/.mktemp-wrapper/mktemp` shim 重定向到 `/tmp/claude/p1d-smoke/`,sandbox-allowed 路径);bundled scripts byte-identical 不破 |
| Run started(local)| 2026-05-07 11:27:00 |
| Run finished(local)| 2026-05-07 11:27:10 |
| Total duration | ~10 秒 |
| Plan ref | `/Users/praise/.claude/plans/joyful-squishing-moonbeam.md`(plan v3 §测试计划 §C-4 6 case fixed fixtures + plan §Plan Review APPROVED 2026-05-07T10:59:04Z) |

## SKILL.md / 4 对照 / bundled 不变性证据

```bash
# cc-skill-stocktake SKILL.md(本 P1d §2 prepend anchor 后基线,C-4 全程不变)
shasum -a 256 ~/.codex/skills/cc-skill-stocktake/SKILL.md
# expected: df16be83df67a04605b61294a210f9ad214b22e55ce40a45f34068dbaa2c48f2

# 4 对照 SKILL.md byte-identical(全 P1d 流程必须保持)
for slug in cc-executable-plan cc-review cc-akb-search cc-local-llm; do
  shasum -a 256 ~/.codex/skills/$slug/SKILL.md
done
# expected (全 byte-identical to baseline):
#   cc-executable-plan: 5af73fdadd91097cc60f8e6c3622427aeaac8382bda0d1525debe564f2c46d0a
#   cc-review:          592cf3ed4b2c6e3f96c63a500bdbe3bebbc375a19983debbfa7f1877f406d274
#   cc-akb-search:      b669380de8a0cd4319f0c978f4cfa69d2757b004a8569fc3adac8f633637f982
#   cc-local-llm:       3a31ebe4f5938864e752ea51d35d89403e62d31a352ac3eaf01a784721c73050

# bundled scripts byte-identical(C-5 hard check #10)
for s in scan quick-diff save-results; do
  shasum -a 256 ~/.codex/skills/cc-skill-stocktake/references/source-bundled-resources/scripts/$s.sh
done
# expected:
#   scan.sh:         b1f16b586e7e2f4c6eeb54ea3895cd5501e64a1e12e242c820193c1193d0b263
#   quick-diff.sh:   e8b48f77d9e34d0c8043f8fede01387947ecfe0e960f7bd04cce9a44e606c0e8
#   save-results.sh: 250968532dc3bf1074f024a710d5d8aeec930e6798269703072f062fad1905bd
```

## 6 case 执行表

| # | case_id | input fixture | output evidence | runner verdict | Claude re-verify |
|---|---------|--------------|-----------------|----------------|------------------|
| 1 | 01-scan | env override → fixtures/skills/{a,b,c} | `outputs/01-scan/raw-output.json` + `assertion-result.json` | **PASS** | **PASS** |
| 2 | 02-quickdiff-nochange | `fixtures/baseline-results.json`(evaluated_at=2030-01-01 → 无 changed) | `outputs/02-quickdiff-nochange/raw-output.json` | **PASS** | **PASS** |
| 3 | 03-quickdiff-changed | `fixtures/changed-results.json`(baseline 缺 skill-c → c is_new) | `outputs/03-quickdiff-changed/raw-output.json` | **PASS** | **PASS** |
| 4 | 04-save-results | stdin = `fixtures/save-results-input.json` + target=fixture path | `outputs/04-save-results/results.json` + `assertion-result.json` | **PASS** | **PASS** |
| 5 | 05-evaluator | `fixtures/evaluator-input.json`(3 fixture skills inventory) | `outputs/05-evaluator/raw-output.json`(JSON array × 3 verdicts) | **PASS** | **PASS** |
| 6 | 06-isolation-failure | (合成):scan outputs / 5 真实 SKILL.md / bundled / 真实 cache + 4 failure injection | `outputs/06-isolation-failure/{6b1-out, 6b1-err, ..., 6b4-err}.txt` + `assertion-result.json` | **PASS** | **PASS** |

**汇总:6/6 case PASS**(runner self-eval + Claude re-verify 双判定一致)。

## 关键 hard fail 防护实测(全未触发)

| 防护 | 触发? |
|------|-------|
| jq / bash 缺失 | ❌(jq 1.7.1 + bash 3.2.57 OK)|
| bundled scripts sha 漂移 → C-4 hard fail | ❌(全程 3/3 byte-identical)|
| scan.sh 出真实 skill 路径 → isolation hard fail | ❌(env override 守住,outputs 0 真实路径命中)|
| quick-diff 接受非法 evaluated_at → C-4 fail | ❌(case 6b.1 实测:invalid → exit=1)|
| save-results 破坏 results.json → C-4 fail | ❌(case 6b.2 实测:non-JSON stdin → exit=1 + 不写 target)|
| mock evaluator 输出 schema 不合格 → C-4 fail | ❌(case 05 全 schema 合规;case 6b.3-4 实测异常输入全 exit≠0)|
| 真实 SKILL.md hash 漂移 | ❌(5/5 byte-identical 守住)|
| 真实 results.json 被写 | ❌(不存在;runner 0 调真实路径)|
| 4 对照 SKILL.md byte-identical 漂移 | ❌(全 byte-identical 守住)|

## reviewer 复测命令

```bash
EV="/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/ai-infra/akb2-toolbox-evals/p1d-cc-skill-stocktake"

# 1. preflight evidence
jq . "$EV/outputs/preflight.json"
# 期望:bundled_scripts 3 sha 与 baseline 一致;mktemp wrapper 路径写明

# 2. 6 case verdict 实测对账
for c in 01-scan 02-quickdiff-nochange 03-quickdiff-changed 04-save-results 05-evaluator 06-isolation-failure; do
  printf "%-25s status=%s reason=%s\n" "$c" \
    "$(jq -r '.status' "$EV/outputs/$c/assertion-result.json")" \
    "$(jq -r '.reason' "$EV/outputs/$c/assertion-result.json")"
done
# 期望 6 行全 status=pass

# 3. case 05 evaluator 输出 schema 验
jq '.[]' "$EV/outputs/05-evaluator/raw-output.json"
# 期望 3 对象,每对象 skill_name/verdict/reason;verdict 5 enum;reason ≥ 30 chars + 含 skill_name

# 4. case 06 isolation:outputs/ 11 evidence JSON 0 命中真实路径
find "$EV/outputs" \( -name "raw-output.json" -o -name "assertion-result.json" \) -type f | \
  xargs grep -l -E '/Users/praise/\.claude/skills|/Users/praise/\.codex/skills'
# 期望 空输出

# 5. SKILL.md / bundled scripts 不变性
shasum -a 256 ~/.codex/skills/cc-skill-stocktake/SKILL.md
shasum -a 256 ~/.codex/skills/cc-skill-stocktake/references/source-bundled-resources/scripts/{scan,quick-diff,save-results}.sh
# 期望 sha 与 baseline 完全一致

# 6. 4 对照 SKILL.md byte-identical
for slug in cc-executable-plan cc-review cc-akb-search cc-local-llm; do
  shasum -a 256 ~/.codex/skills/$slug/SKILL.md | cut -d' ' -f1
done
# 期望 4 行 与 baseline 一致

# 7. 重跑某 case 单独验证
bash "$EV/run-smoke.sh" --case 05
```

## 评估结论

- **6/6 case PASS**(双独立判定一致)
- **0 hard fail 触发**
- **0 LLM call / 0 网络 / 0 localhost 依赖**(deterministic mock evaluator)
- **0 user-handoff**(全 Claude 自动)
- **bundled scripts byte-identical 守住**(C-5 hard check #10 通过)
- **5 真实 SKILL.md byte-identical 守住**(本 P1d 只 prepend cc-skill-stocktake/SKILL.md anchor;4 对照不动)
- **真实 results.json 0 触**(不存在 + runner 0 调真实路径)
- **isolation 守住**(11 evidence JSON 0 命中真实 skill 路径)

## 升级提议

- **eval_level: L0 → L1**:6/6 case + preflight + bundled scripts byte-identical + isolation + failure injection 实测充分(deterministic + 不调外部 LLM + 0 LLM judgment 质量声称)
- **status: candidate → active**:**待 §6 C-5 Tool Intake Gate 10 项 hard checks 全 pass 后才升**(本 §4 不动 status)
- **不升 L2**:per user 硬边界 + plan v3 §非目标(LLM judgment 真实质量 + 多轮 semantic + Full Stocktake → L2 独立 plan)
- **automation_allowed: false** 全程保持
- **runtime_mutation_allowed: false** 全程保持

## 链接

- correction / phase report:`AKB2.0/docs/phase-c-reports/p1d-cc-skill-stocktake-l1-gate-2026-05-07.md`
- baseline snapshot:`baseline-snapshot.md`(本 evidence pack)
- case-register:`./case-register.md`
- assertions(含本轮实测对账):`./assertions.md`
- run-smoke.sh runner:`./run-smoke.sh`
- 6 fixture:`./fixtures/{skills/{a,b,c}, baseline-results.json, changed-results.json, invalid-results.json, save-results-input.json, evaluator-input.json, evaluator-mock.sh}`
- 6 outputs:`./outputs/{01-06}/{raw-output, assertion-result}.json` + `outputs/preflight.json`
- plan v3:`/Users/praise/.claude/plans/joyful-squishing-moonbeam.md`(本 evidence 写到 plan §Review 2 记录后,§C-5 决策时引用)
