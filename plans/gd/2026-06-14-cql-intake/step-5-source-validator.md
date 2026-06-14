# Step 5: source_validator live + discovery listing

GD_STANDARD: Project GD/prompts/gd-review-standard.md
TEMPLATE_KIND: gd-step-plan

日期: 2026-06-14 | 状态: draft

---

## 1. 目标链

```text
TASK_GOAL: source_validator live 模式接真（真 trial-fetch 76 源）；以 run_id 原子写 sources-ledger.jsonl；discovery listing 路径接 scrapling；新建 test_source_validator.py。
```

REVIEW_DOMAIN: ai_infra
REVIEW_FOCUS: `source_validator live 76 源硬断言(F6); unique_urls==config_urls(F6); run_id 原子写入(P3); listing 接 scrapling`

---

## 3. 成功标准（SC）

- [ ] SC-9（source_validator mocked 单测）: 默认跑，覆盖 dry-run 结构、CLI 可用、ledger 字段完整
  - verify (method: test): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && python -m pytest tests/quality/test_source_validator.py -q -m "not integration"`
  - expect: exit 0

- [ ] SC-10（source_validator live 门—integration）: --live 模式产 ledger；76 行、unique_urls==config_urls、必填字段（feed/url/reachable/latest_post/trial_fetch）齐、reachable≥70；同次运行 run_id 一致；run_id 原子写入不混旧记录
  - verify (method: command): `cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2" && AKB2_LIVE_VALIDATE=1 PYTHONPATH=src python -m akb2.quality.intake.source_validator --live --out reports/source-validation/sources-ledger.jsonl && python -c "import json,yaml; cfg=yaml.safe_load(open('config/main_sources.yaml'))['sources']; c_urls={s.get('feed_url') or s.get('listing_url') for s in cfg}-{None,''}; rs=[json.loads(l) for l in open('reports/source-validation/sources-ledger.jsonl')]; assert len(rs)==76,'rows!=76'; u={r['url'] for r in rs}; assert u==c_urls,'url mismatch'; req_fields={'feed','url','reachable','latest_post','trial_fetch'}; bad=[i for i,r in enumerate(rs) if not req_fields.issubset(r.keys())]; assert not bad,f'missing fields at rows {bad}'; ok=sum(1 for r in rs if r.get('reachable')); assert ok>=70,f'reachable={ok}<70'; run_ids={r.get('run_id') for r in rs}; assert len(run_ids)==1,f'mixed run_ids: {run_ids}'; print('PASS')"`
  - expect: PASS; 76 行 ledger，unique_urls==config_urls，必填字段齐，reachable≥70，run_id 一致

---

## 6. 实现步骤

```text
Step.1
  WHERE: src/akb2/quality/intake/source_validator.py（_validate_source_live:67）
  WHAT:  真 trial-fetch（httpx）；必填字段（feed/url/reachable/latest_post/trial_fetch）；以 run_id 原子写 sources-ledger.jsonl（写临时文件再 rename，非 append）；失败记录保留
  WHY:   F6 live 是 skeleton；P3 append 混旧记录
  VERIFY: AKB2_LIVE_VALIDATE=1 PYTHONPATH=src python -m akb2.quality.intake.source_validator --live --out reports/source-validation/sources-ledger.jsonl && python -c "import json,yaml; cfg=yaml.safe_load(open('config/main_sources.yaml'))['sources']; c_urls={s.get('feed_url') or s.get('listing_url') for s in cfg}-{None,''}; rs=[json.loads(l) for l in open('reports/source-validation/sources-ledger.jsonl')]; assert len(rs)==76; u={r['url'] for r in rs}; assert u==c_urls; ok=sum(1 for r in rs if r.get('reachable')); assert ok>=70; ok_fields=sum(1 for r in rs if r.get('reachable') and r.get('trial_fetch') and r.get('latest_post')); assert ok_fields>=60,f'reachable+nonempty={ok_fields}<60'; run_ids={r.get('run_id') for r in rs}; assert len(run_ids)==1; print('PASS')"

Step.2
  WHERE: tests/quality/test_source_validator.py（新建）
  WHAT:  mocked 单测：dry-run 结构、ledger 字段完整、run_id 存在；integration 测试用 @pytest.mark.integration 标记，默认不跑
  WHY:   SC-9/SC-10 测试覆盖；integration 测试不污染默认 pytest 跑
  VERIFY: python -m pytest tests/quality/test_source_validator.py -q -m "not integration"
```
