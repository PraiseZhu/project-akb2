VERDICT: REQUIRES_CHANGES

## 覆盖维度清单

- 目标链：已核对 step-2/step-3 的 PROJECT_GOAL/CHAIN_GOAL/PHASE_GOAL，分别锚到 `docs/constitution.md` 原则 3/7/8 与 `specs/content-lane-quality/spec.md` SC-001/003/004，目标不是空转套话。
- P1 决策契约：已核对 `decision-manifest.json` 的 `fixture_id/verdict/expected_after_p2` 契约、用户拍板点、step-3 SC-8 消费方式。
- SC 可验证性：已核对 step-2 SC-7、step-3 SC-8 两条 verify、`p2-e-verify-ceiling` 创建动作、`gd-review-suite-controller.py --kind combined` 命令。
- SC-8 验收器脚本：已读 `scripts/verify_loop1_reachable_ceiling.py`，并实跑 `--self-test`、`--help`、`ruff check`、合成反例。
- anti-fill：已读取 `/Users/praise/AI-Agent/Claude/projects/Project GD/fixtures/anti-fill/banned-generic-tokens.json`，扫描 step-2/step-3；只命中 step-3:8 状态说明中的 `细化`，不在 SC 或实现步骤中，未构成 anti-fill 阻塞。
- 宪法/护栏：已核对完整性地板不可补偿、异族审、负样本仍拒、"该拒"不硬凑入库。

## Findings

### [P1] `reject` 验收被脚本降级成 `passed=False`，不能证明"该拒全拒"

位置：
- `plans/gd/2026-06-28-loop1-content-quality/step-3-p2-classified-remediation.md:31`
- `plans/gd/2026-06-28-loop1-content-quality/step-3-p2-classified-remediation.md:33`
- `plans/gd/2026-06-28-loop1-content-quality/master-plan.md:49`
- `scripts/verify_loop1_reachable_ceiling.py:8`
- `scripts/verify_loop1_reachable_ceiling.py:102`

问题：
step-3 与 master 都要求 `expected_after_p2=reject` 的样本必须 `rejected`，但脚本实际只在 `expected == "reject"` 时检查 `passed` 不能为 true；任何 `passed=False` 的失败样本都会被当作 reject 通过。当前真实 `reports/calibration/factory.json` 已显示 `final_status` 同时存在 `failed`、`rejected`、`rejected_correctly`，两者语义不同；`failed` 代表还卡在 repair/compose 等链路，不等于被正确拒收。

实跑反例：

```text
contradict_verdict_expected ok= True failures= []
failed_not_rejected_accepted_as_reject ok= True failures= []
```

其中第二条是 `expected_after_p2=reject`、factory entry 为 `passed=False, final_status="failed", failure_segment="repair_loop"`，脚本仍返回 ok。

影响：
P2 可以把一个本该正确拒收的样本留在 repair_loop failed 状态，却通过 SC-8 第一条验收；这会让"够格全过 + 该拒全拒"失去机器可判性，并削弱宪法原则 3 fail-closed 与原则 8 实测价值裁决。

可直接落地的修法：
把脚本里的 reject 判定改成显式 rejected 语义，并同步 docstring/self-test。

建议在 `scripts/verify_loop1_reachable_ceiling.py` 增加：

```python
def _is_rejected(entry: dict[str, Any]) -> bool:
    if "rejected" in entry:
        return entry.get("rejected") is True
    return entry.get("final_status") in {"rejected", "rejected_correctly"}
```

然后把 `elif expected == "reject" and passed:` 改为：

```python
elif expected == "reject" and not _is_rejected(match):
    failures.append(
        f"fixture_id={fid!r} P1 判 reject 但 factory 未显示 rejected "
        f"(passed={match.get('passed')!r}, final_status={match.get('final_status')!r}, "
        f"failure_segment={match.get('failure_segment')!r})"
    )
```

同时新增 self-test：
- `expected_after_p2=reject` + `passed=False` + `final_status="failed"` 必须失败。
- `expected_after_p2=reject` + `final_status="rejected"` 必须通过。
- 如要覆盖负样本语义，`final_status="rejected_correctly"` 也应通过。

验收命令：

```bash
PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 scripts/verify_loop1_reachable_ceiling.py --self-test
ruff check scripts/verify_loop1_reachable_ceiling.py
```

### [P1] `verdict` 与 `expected_after_p2` 没有不变量，用户拍板可被 manifest 字段绕过

位置：
- `plans/gd/2026-06-28-loop1-content-quality/step-2-p1-semantic-audit.md:31`
- `plans/gd/2026-06-28-loop1-content-quality/step-2-p1-semantic-audit.md:34`
- `plans/gd/2026-06-28-loop1-content-quality/step-2-p1-semantic-audit.md:49`
- `plans/gd/2026-06-28-loop1-content-quality/step-2-p1-semantic-audit.md:58`
- `scripts/verify_loop1_reachable_ceiling.py:88`

问题：
step-2 把 P1 定义成唯一需要用户拍板的 `{救|调|拒}` 决策门，但 SC-7 的机器校验只检查 `verdict` 和 `expected_after_p2` 各自属于枚举集合，没有约束两者关系。step-3 的 SC-8 验收器又只消费 `expected_after_p2`，不消费 `verdict` 的语义。合成反例证明 `{"verdict":"拒","expected_after_p2":"ingest"}` 在样本 `passed=True` 时会被脚本接受。

影响：
用户确认"拒"后，manifest 仍可把同一 fixture 写成 `expected_after_p2=ingest`；或用户确认"救/调"后，manifest 可写成 `reject`。这样 P1 决策门不再是 P2 的可信输入，step-3 的四条路径也会被错误触发。

可直接落地的修法：
在 step-2 SC-7、manifest 契约、脚本三处加同一条不变量：

```text
verdict in {"救", "调"} => expected_after_p2 == "ingest"
verdict == "拒"        => expected_after_p2 == "reject"
```

把 step-2 `verify-2` 改成带不变量的命令，例如：

```bash
python3 -c "import json;m=json.load(open('reports/loop1-audit/decision-manifest.json'));entries=m['entries'];assert entries;assert all(e['verdict'] in ('救','调','拒') and e['expected_after_p2'] in ('ingest','reject') and ((e['verdict'] in ('救','调') and e['expected_after_p2']=='ingest') or (e['verdict']=='拒' and e['expected_after_p2']=='reject')) for e in entries)"
```

并在 `scripts/verify_loop1_reachable_ceiling.py` 的循环内加：

```python
if (verdict in ("救", "调") and expected != "ingest") or (verdict == "拒" and expected != "reject"):
    failures.append(
        f"fixture_id={fid!r} verdict={verdict!r} 与 expected_after_p2={expected!r} 矛盾"
    )
    continue
```

如确实要允许用户 override，必须另加 `user_override_reason`，且 step-2 审计记录与 manifest 同时引用用户确认文本；否则不要放开。

验收命令：

```bash
python3 -c "import importlib.util;spec=importlib.util.spec_from_file_location('v','scripts/verify_loop1_reachable_ceiling.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);ok,_=m.check_reachable_ceiling({'negative_reject_rate':1.0,'entries':[{'index':1,'passed':True,'final_status':'ingested'}]},{'entries':[{'fixture_id':1,'verdict':'拒','expected_after_p2':'ingest'}]});assert not ok"
```

### [P2] SC-7 没有机器校验 manifest 覆盖全部 P0 后剩余正样本失败集

位置：
- `plans/gd/2026-06-28-loop1-content-quality/step-2-p1-semantic-audit.md:31`
- `plans/gd/2026-06-28-loop1-content-quality/step-2-p1-semantic-audit.md:33`
- `plans/gd/2026-06-28-loop1-content-quality/step-2-p1-semantic-audit.md:34`
- `plans/gd/2026-06-28-loop1-content-quality/step-3-p2-classified-remediation.md:33`

问题：
SC-7 写了 `decision-manifest.json` 覆盖全部剩余失败篇，但 `verify-1` 只用 `wc -l` 对审计 md 数量做人工式检查，`verify-2` 只要求 `entries` 非空且字段枚举合法，没有把 manifest 的 fixture 集合与 P0 后 `factory.json` 的失败正样本集合做集合相等断言。step-3 SC-8 只复验 manifest 中列出的目标；manifest 漏一篇时，漏掉的失败篇不会进入 P2 验收目标集。

影响：
P1 审计可能少列某篇，P2 仍可对 manifest 内样本通过 SC-8，导致"满仓可达上限"目标集不完整。

可直接落地的修法：
给 `decision-manifest.json` 增加 `source_factory_calibration_ts` 或 `source_factory_sha256`，并在 SC-7 加一个集合相等校验：P0 后 `factory.json` 中 `expected=="ingest" and passed is False` 的 entry 必须与 manifest entry 的 `fixture_id` 集合完全一致。

示例校验命令：

```bash
python3 -c "import json,re;fac=json.load(open('reports/calibration/factory.json'));m=json.load(open('reports/loop1-audit/decision-manifest.json'));want={str(e['index']) for e in fac['entries'] if e.get('expected')=='ingest' and e.get('passed') is False};seen={str(int(re.search(r'\\d+',str(e['fixture_id'])).group(0))) for e in m['entries']};assert seen==want,(sorted(want-seen),sorted(seen-want))"
```

若某些 P0 后失败正样本已经在 admission/integrity_floor 被正确拒收，也仍应在 P1 manifest 中以 `verdict="拒", expected_after_p2="reject"` 明列，不能隐式遗漏。

## 已核对通过的关键点

- step-2 的目标链明确：P0 真实 trace/lane_reason 是 P1 输入，P1 只做只读语义审计，用户在 step 末确认每篇 `救/调/拒`，没有预设必须救活全部样本。
- step-3 的四条路径与 P1 定性绑定清楚：`救 -> repair`、`调 -> rubric.yaml`、`拒 -> corpus reject/移出`、lane/reject/test debt 基于 lane_reason 并行处理。
- step-3 明确 `p2-e-verify-ceiling` 要创建 `scripts/verify_loop1_reachable_ceiling.py`，并列出 `reports/p2/execution-result.json` 与 `reports/p2/diff.patch` 给 combined review 消费。
- step-3 有护栏：rubric 调整不改判定代码，负样本仍 100% 拒；完整性地板/F4/novelty 不放宽；"该拒"不硬凑入库。
- `gd-review-suite-controller.py --kind combined` 的真实入口已写入 SC-8 第二条 verify，且要求 aggregate `GD_REVIEW_DECISION: APPROVED`。

## 实际跑过的验证命令

```bash
pwd && git status --short --branch
nl -ba plans/gd/2026-06-28-loop1-content-quality/step-2-p1-semantic-audit.md | sed -n '1,220p'
nl -ba plans/gd/2026-06-28-loop1-content-quality/step-3-p2-classified-remediation.md | sed -n '1,260p'
nl -ba scripts/verify_loop1_reachable_ceiling.py | sed -n '1,320p'
python3 - <<'PY'
import json
from pathlib import Path
p=Path('/Users/praise/AI-Agent/Claude/projects/Project GD/fixtures/anti-fill/banned-generic-tokens.json')
obj=json.loads(p.read_text())
print(obj['tokens'])
PY
PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 scripts/verify_loop1_reachable_ceiling.py --self-test
PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH python3 scripts/verify_loop1_reachable_ceiling.py --help
PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH ruff check scripts/verify_loop1_reachable_ceiling.py
python3 - <<'PY'
import json
from pathlib import Path
o=json.loads(Path('reports/calibration/factory.json').read_text())
print(sorted(set(str(e.get('final_status')) for e in o.get('entries', []) if isinstance(e, dict))))
PY
python3 - <<'PY'
import importlib.util
from pathlib import Path
spec=importlib.util.spec_from_file_location('v', Path('scripts/verify_loop1_reachable_ceiling.py'))
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
factory={'negative_reject_rate':1.0,'entries':[{'index':1,'passed':True,'final_status':'ingested'},{'index':2,'passed':False,'final_status':'failed','failure_segment':'repair_loop'}]}
for name,dec in [
    ('contradict_verdict_expected', {'entries':[{'fixture_id':1,'verdict':'拒','expected_after_p2':'ingest'}]}),
    ('failed_not_rejected_accepted_as_reject', {'entries':[{'fixture_id':2,'verdict':'拒','expected_after_p2':'reject'}]}),
]:
    ok, failures=mod.check_reachable_ceiling(factory, dec)
    print(name, 'ok=', ok, 'failures=', failures)
PY
python3 - <<'PY'
import json
from pathlib import Path
b=Path('/Users/praise/AI-Agent/Claude/projects/Project GD/fixtures/anti-fill/banned-generic-tokens.json')
tokens=json.loads(b.read_text())['tokens']
paths=[Path('plans/gd/2026-06-28-loop1-content-quality/step-2-p1-semantic-audit.md'),Path('plans/gd/2026-06-28-loop1-content-quality/step-3-p2-classified-remediation.md')]
for p in paths:
    print('FILE',p)
    for i,line in enumerate(p.read_text().splitlines(),1):
        hits=[t for t in tokens if t.lower() in line.lower()]
        if hits:
            print(f'{i}: {hits}: {line}')
PY
rg -n "原则 3|原则 6|原则 7|原则 8|fail-closed|完整性地板|异族|实测价值|不可被" docs/constitution.md
rg -n "SC-001|SC-003|SC-004|FR-010|negative|reject|rubric|概念|对抗" specs/content-lane-quality/spec.md
rg -n "SC-8|SC-7|decision-manifest|expected_after_p2|verify_loop1|够格|该拒|negative_reject_rate" plans/gd/2026-06-28-loop1-content-quality/master-plan.md
```

关键输出：

```text
SELF_TEST_PASSED
All checks passed!
factory final_status: failed / rejected / rejected_correctly
anti-fill scan: step-2 无命中；step-3 仅 line 8 状态说明命中 "细化"，非 SC/实现步骤
```
