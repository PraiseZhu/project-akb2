# P1d C-4 Assertions Register — cc-skill-stocktake

per plan v3 §测试计划 §C-4 6 case;runner = `run-smoke.sh`(Bash + jq + deterministic mock evaluator)。
6 case status 由 `outputs/<case>/assertion-result.json` 持久化;Claude 独立 re-verify 见 §本轮实测对账 部分。

## Case 01 — scan(bundled scan.sh)

| # | assertion | 期望 | runner-side | Claude re-verify |
|---|-----------|------|-------------|------------------|
| 01.1 | scan.sh exit 0 + JSON parse OK | exit=0, stdout JSON | ✅ | ✅ |
| 01.2 | skills[] length ≥ 3 | ≥ 3 | ✅(实测 6,因 GLOBAL+PROJECT 都指 fixture)| ✅ |
| 01.3 | 含 fixture skill-a / skill-b / skill-c | 3 全在 | ✅ | ✅ |
| 01.4 | 每 skill 有 name/description/mtime/use_7d/use_30d/path 字段 | 全 | ✅ | ✅ |
| 01.5 | 输出不含 `/Users/praise/.claude/skills` / `/Users/praise/.codex/skills`(isolation)| 0 命中 | ✅ | ✅ |

## Case 02 — quick-diff no-change

| # | assertion | 期望 | runner-side |
|---|-----------|------|-------------|
| 02.1 | quick-diff.sh exit 0 + JSON parse OK | exit=0, JSON | ✅ |
| 02.2 | 输出 = `[]` | length=0 | ✅ |

## Case 03 — quick-diff changed/new

| # | assertion | 期望 | runner-side |
|---|-----------|------|-------------|
| 03.1 | quick-diff.sh exit 0 + JSON parse OK | exit=0, JSON | ✅ |
| 03.2 | 输出 array length ≥ 1 | ≥ 1 | ✅(length=6,3 fixture × 2 dir)|
| 03.3 | skill-c 有 is_new=true(changed-results.json baseline 缺 c → 算 new)| ≥ 1 hit | ✅ |

## Case 04 — save-results merge

| # | assertion | 期望 | runner-side |
|---|-----------|------|-------------|
| 04.1 | save-results.sh exit 0 + target 文件存在 | exit=0, 文件存在 | ✅ |
| 04.2 | evaluated_at 是 ISO UTC(`^YYYY-MM-DDTHH:MM:SSZ$`)| match | ✅ |
| 04.3 | skills dict 含 ≥ 3 项 | ≥ 3 | ✅ |
| 04.4 | target path 不指真实 cache(`~/.claude/skills/skill-stocktake/results.json`)| ≠ 真实 path | ✅(target = fixture outputs/04-save-results/results.json)|

## Case 05 — bounded evaluator(deterministic mock)

| # | assertion | 期望 | runner-side |
|---|-----------|------|-------------|
| 05.1 | evaluator-mock.sh exit 0 | exit=0 | ✅ |
| 05.2 | 输出 JSON parse + type=array | array | ✅ |
| 05.3 | array length = 3 | 3 | ✅ |
| 05.4 | 每对象有 skill_name / verdict / reason keys | 全 | ✅ |
| 05.5 | verdict ∈ 5 enum(Keep / Improve / Update / Retire / Merge into <X>)| match regex | ✅ |
| 05.6 | reason 长度 ≥ 30 chars(per row)| ≥ 30 | ✅ |
| 05.7 | reason 含 skill_name(per row,self-referential)| 全含 | ✅(skill-a / skill-b / skill-c per row)|
| 05.8 | reason 不含 forbidden token(`unchanged` / `^same$` / `as before` / `n/a` / `no change`)| 0 命中 | ✅ |
| 05.9 | unique verdict count ≥ 2(防全 mock 同一 placeholder)| ≥ 2 | ✅(实测 3:Keep + Improve + Retire) |

## Case 06 — isolation + failure injection

### 6a isolation

| # | assertion | 期望 | runner-side |
|---|-----------|------|-------------|
| 6a.1 | outputs/<case>/{raw-output, assertion-result}.json 全 11 evidence JSON 不含真实 skill 路径 | 0 hits | ✅ |
| 6a.2 | 5 真实 SKILL.md sha256 仍 byte-identical(cc-skill-stocktake / cc-{executable-plan, review, akb-search, local-llm}) | 全 ✓ | ✅ |
| 6a.3 | bundled scripts(scan/quick-diff/save-results)sha256 仍 byte-identical(C-5 hard check #10)| 全 ✓ | ✅ |
| 6a.4 | 真实 `~/.claude/skills/skill-stocktake/results.json` sha256 不变(若存在)| 不变 / 不存在 | ✅(不存在,0 风险)|

### 6b failure injection

| # | assertion | 期望 | runner-side |
|---|-----------|------|-------------|
| 6b.1 | quick-diff.sh < invalid-results.json(非法 evaluated_at)→ exit 非 0 | exit ≠ 0 | ✅(exit=1)|
| 6b.2 | save-results.sh < "not json content"(stdin 非 JSON)→ exit 非 0 + 不写 target 文件 | exit ≠ 0 + 文件不存在 | ✅(exit=1, target 不存在)|
| 6b.3 | evaluator-mock.sh < "not json"(stdin 非 JSON)→ exit 非 0 | exit ≠ 0 | ✅(exit=1)|
| 6b.4 | evaluator-mock.sh < unknown skill name → exit 非 0 | exit ≠ 0 | ✅(exit=2)|

## 本轮实测对账(2026-05-07,user 跑 `bash run-smoke.sh --all` + Claude re-verify)

### Run metadata

| 字段 | 值 |
|------|------|
| Run timestamp(local)| 2026-05-07 11:27 |
| Total duration | ~10 秒(6 case 顺序)|
| Runner | `Codex/ai-infra/akb2-toolbox-evals/p1d-cc-skill-stocktake/run-smoke.sh` |
| Execution mode | **Claude 全自动**(Bash + jq + deterministic mock evaluator;0 LLM call;0 网络;0 user-handoff)|
| Sandbox workaround | mktemp PATH wrapper(/tmp/claude/p1d-smoke/);bundled scripts byte-identical 守住 |
| jq / bash | jq-1.7.1 / bash 3.2.57 ✓ |
| bundled scripts byte-identical | 3/3 ✓(scan / quick-diff / save-results)|
| 真实 cache(`~/.claude/skills/skill-stocktake/results.json`)| 不存在(0 风险) |

### 6 case 实测 PASS/FAIL 表

| # | case_id | runner verdict | 关键证据 |
|---|---------|----------------|---------|
| 1 | 01-scan | **PASS** | skill_count=6;3 fixture 全在;字段齐;0 真实路径命中 |
| 2 | 02-quickdiff-nochange | **PASS** | 输出 `[]`,length=0 |
| 3 | 03-quickdiff-changed | **PASS** | length=6;skill-c is_new=true 命中 |
| 4 | 04-save-results | **PASS** | evaluated_at=2026-05-07T11:27:08Z;skills_count=3;target=fixture path |
| 5 | 05-evaluator | **PASS** | 3 verdict 全 schema 合规(Keep / Improve / Retire);reason 全 ≥ 30 chars + 含 skill_name + 不含 forbidden;3 unique verdicts |
| 6 | 06-isolation-failure | **PASS** | 6a 4 项 isolation 全过(0 真实路径 + 5 SKILL.md byte-identical + 3 bundled byte-identical + 真实 cache 0 写)+ 6b 4 failure injection 全 hard fail(exit 全 ≠ 0)|

**汇总:6/6 PASS**。

## 总 verdict

**C-4 L1 smoke verdict:6/6 PASS**(runner-side + Claude re-verify 双独立判定一致)。

按 plan v3 §4 收尾要求:
- 6/6 pass → 进 §5 Review 2(ai_infra)
- Review 2 APPROVED 后 + plan §Review 2 记录 writeback → 进 §6 C-5 Tool Intake Gate
- C-5 verdict 三档 anchor 决策(rollback / patch / accept)

**进入 §5 Review 2**。
