REVIEWER: claude_sc8_final
REVIEW_TARGET: master-plan.md SC-8 verify command
REVIEW_KIND: plan
REVIEW_RUN_STATUS: completed
GD_REVIEW_DECISION: APPROVED

---

## 验证明细

### 验证点 1 — python -c 语法正确性

- 外层引号：双引号包裹整条 -c 字符串，内层字符串字面量均用单引号，无冲突
- f-string：`f'config sources={len(sources)}!=76'` 等格式正确，大括号无遗漏
- 语句分隔：全部用分号，无换行依赖，单行可执行
- 集合字面量 `{None,''}` 合法 Python set；`req_fields={'feed','url','reachable','latest_post','trial_fetch'}` 同
- 括号配对：`sum(1 for r in rs if r.get('reachable'))` 闭合正确
- **结论：语法 PASS**

### 验证点 2 — config_urls 集合运算

```python
config_urls = {s.get('feed_url') or s.get('listing_url') for s in sources} - {None,''}
```

- `s.get('feed_url') or s.get('listing_url')`：feed_url 非空非假则取之，否则 fallback 到 listing_url；两者皆无则产出 None
- `- {None,''}` 差集：过滤掉 None 和空字符串，确保集合只含有效 URL
- 逻辑正确，不含 None/空串
- **结论：集合运算 PASS**

### 验证点 3 — assert unique_urls == config_urls 语义

- `unique_urls = {r['url'] for r in rs}`：ledger 中所有 url 去重集合
- `config_urls`：yaml 中所有源的有效 feed/listing url 集合
- 两集合相等 ⟺ ledger 覆盖的 URL 与 config 完全一致（无多余、无缺失）
- 错误消息附带 `extra` / `missing` 差集，便于定位
- **结论：逻辑满足"内容与 config 一致"PASS**

### 验证点 4 — 端到端可执行性

| 检查项 | 状态 |
|--------|------|
| `cd` 路径绝对，引号包裹（含空格） | PASS |
| `import json,yaml` — json 标准库，yaml 为 pyyaml（项目已依赖） | PASS |
| `open('config/main_sources.yaml')` 相对路径在 cd 后有效 | PASS |
| `open('reports/source-validation/sources-ledger.jsonl')` 同上 | PASS |
| `assert len(sources)==76` 及后续 assert 均附可读 f-string | PASS |
| `print('PASS')` 与 expect `PASS` 一致 | PASS |
| `assert ok>=70` 阈值合理（76 源中至少 70 可达） | PASS |

**结论：端到端可执行 PASS**

---

## 总结

SC-8 verify 命令的五项断言（config 数量、ledger 行数、URL 唯一性、URL 集合一致、字段完整性）加可达阈值断言，覆盖了 SC-8 成功标准的全部可机验部分。语法、逻辑、可执行性均无问题。
