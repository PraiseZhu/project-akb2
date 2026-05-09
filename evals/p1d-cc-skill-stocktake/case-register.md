# P1d C-4 Case Register — cc-skill-stocktake

Phase:P1d L1 smoke
工具:`cc-skill-stocktake`
SKILL.md sha256(P1d §2 后基线):`df16be83df67a04605b61294a210f9ad214b22e55ce40a45f34068dbaa2c48f2`
Bundled scripts(byte-identical 守门):scan.sh `b1f16b586e7e` / quick-diff.sh `e8b48f77d9e3` / save-results.sh `250968532dc3`
Execution mode:**Claude 全自动**(Bash + jq + deterministic mock evaluator;0 LLM call;0 网络;0 localhost;0 user-handoff)
Plan ref:`/Users/praise/.claude/plans/joyful-squishing-moonbeam.md` §P1d v3

## 6 case 矩阵

| # | case_id | 用途 | runner verdict | 关键 assertion |
|---|---------|------|----------------|----------------|
| 1 | 01-scan | bundled scan.sh + env override | **PASS** | scan_summary 完整;skills[] ≥ 3 + 含 fixture skill-a/b/c;字段齐(name/desc/mtime/use_7d/use_30d/path);0 真实路径命中 |
| 2 | 02-quickdiff-nochange | bundled quick-diff.sh no-change | **PASS** | 输出 `[]`(baseline evaluated_at=2030 → fixture mtime < baseline → 无 changed)|
| 3 | 03-quickdiff-changed | bundled quick-diff.sh changed/new | **PASS** | length ≥ 1;skill-c is_new=true(changed-results.json baseline 缺 skill-c → 算 new)|
| 4 | 04-save-results | bundled save-results.sh merge | **PASS** | evaluated_at ISO UTC;skills 3 项 merge;target=fixture path(不指真实 cache)|
| 5 | 05-evaluator | deterministic mock evaluator | **PASS** | JSON array × 3;verdict 5 enum 全在;reason ≥ 30 + 含 skill_name + 不含 forbidden token;3 unique verdicts(Keep/Improve/Retire)|
| 6 | 06-isolation-failure | 真实路径 0 触 + 4 failure injection 全 hard fail | **PASS** | 6a:真实路径 0 命中(只查 raw-output + assertion-result 11 evidence JSON)+ 5 真实 SKILL.md byte-identical + bundled scripts byte-identical + 真实 results.json 不存在;6b.1-4:quick-diff 非法 evaluated_at exit≠0 / save-results 非 JSON stdin exit≠0+不写target / mock evaluator 非 JSON stdin exit≠0 / mock evaluator unknown skill exit≠0 |

## 实测 verdict 分布

| case_id | runner | Claude re-verify(详见 assertions.md)|
|---------|--------|----------------------------------|
| 01-scan | PASS | PASS(scan_summary + 3 fixture 命中 + 字段齐 + 0 真实路径)|
| 02-quickdiff-nochange | PASS | PASS([]→length=0)|
| 03-quickdiff-changed | PASS | PASS(skill-c is_new=true)|
| 04-save-results | PASS | PASS(evaluated_at=ISO + skills_count=3 + fixture path)|
| 05-evaluator | PASS | PASS(JSON array × 3 + verdict 5 enum + reason 全 schema 合规 + 3 unique)|
| 06-isolation-failure | PASS | PASS(6a isolation 4 项 + 6b 4 failure injection 全 hard fail)|

**汇总:6/6 PASS**(runner self-eval 全 pass)。

## Sandbox workaround note

bundled scripts 用 `mktemp -d`(default `/var/folders` 路径)被 macOS sandbox 拒。

**workaround**:run-smoke.sh 在 `$PATH` 前置 `mktemp` shim(`<evidence>/.mktemp-wrapper/mktemp`),把 `mktemp -d` no-args 重定向到 `/tmp/claude/p1d-smoke/wrap-dir.XXXXXX`(sandbox-allowed 路径)。**不修改 bundled scripts byte sha**(bundled scripts 自己仍调 `mktemp` literal,只是 PATH lookup 找到 wrapper 而非 `/usr/bin/mktemp`)。Wrapper 对 explicit template(如 save-results 用的 `mktemp "$RESULTS.XXXXXX"`)passthrough 到 `/usr/bin/mktemp`。

**bundled scripts byte-identical 验**(C-5 hard check #10):3/3 byte-identical 全程守住(per preflight 实测 + case 06 二次验)。

## Hard fail 触发(6 case 全无)

| 类型 | 触发? |
|------|-------|
| jq / bash 缺失 | ❌ 未触发 |
| bundled scripts sha 漂移 | ❌ 未触发(全 byte-identical) |
| scan.sh env override 失败(出真实路径)| ❌ 未触发(0 命中) |
| quick-diff 接受非法 evaluated_at | ❌ 未触发(case 6b.1 实测拒)|
| save-results stdin 非 JSON 破坏文件 | ❌ 未触发(case 6b.2 实测拒 + 不写 target)|
| mock evaluator 输出 schema 不合格 | ❌ 未触发(case 05 全 schema 合规)|
| 真实 SKILL.md hash 漂移 | ❌ 未触发(5 全 byte-identical)|
| 真实 results.json 被写 | ❌ 未触发(不存在)|
| 4 对照 SKILL.md 任一 hash 漂移 | ❌ 未触发(全 byte-identical)|

## 评估流程

1. ✅ user 已批准 plan v3 implementation(2026-05-07T10:59:04Z Codex plan APPROVED)
2. ✅ §1 baseline + §2 P1d-0 + §3 Review 1(initial REQUIRES_CHANGES → followup APPROVED)
3. ✅ §4 C-4 6/6 PASS(本表)
4. ⏳ §5 Review 2(ai_infra)
5. ⏳ §6 C-5 Tool Intake Gate(10 hard checks)
6. ⏳ §7 收尾(5 件套升 active/L1)
