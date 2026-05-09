# P1c C-4 Assertions Logic — cc-local-llm

> 本文件描述 5 case 的 assertion 逻辑(runner-side 实现 in `run-smoke.sh`);per-run 实测结果由 runner 自动落 `outputs/<case>/assertion-result.json`。
> Claude 在 user 跑完 `run-smoke.sh --all` 后会读 outputs + 在文件末尾写"本轮实测对账"段。

## 通用规则(每 case 都套用)

| # | 规则 | 实现 |
|---|------|------|
| G1 | HTTP 200 from `/v1/chat/completions` | `curl -w %{http_code}` 检测 |
| G2 | `choices[0].message.content` 非空(去 trailing whitespace 后) | `jq -r '.choices[0].message.content'` + tr -d 检测 |
| G3 | 非 reasoning-only(content 空但 `reasoning_content` 非空 → fail) | jq 双取 + 比 |
| G4 | 任一 hard fail → 该 case status: fail;无 fallback 重试 | per case stop on first fail |

## Case 01 — 摘要

| # | assertion | 期望 |
|---|-----------|------|
| 01.1 | content 非空非 reasoning-only | G2 + G3 |
| 01.2 | 含字符串 `星澜研究院` | grep |
| 01.3 | 含字符串 `llama-swap` | grep |
| 01.4 | 字符长度 ∈ [12, 60] | `wc -m` |
| 01.5 | 不含 `Ollama` 或 `11434`(防输入污染) | grep -E |

任一 fail → case 01 status: fail。

## Case 02 — 分类

| # | assertion | 期望 |
|---|-----------|------|
| 02.1 | content 非空非 reasoning-only | G2 + G3 |
| 02.2 | trim 后精确匹配 `后端契约` | string compare |
| 02.3 | 不含其它 2 个候选 `性能优化` / `安全审计` | grep -E |

任一 fail → case 02 status: fail。

## Case 03 — JSON 抽取

| # | assertion | 期望 |
|---|-----------|------|
| 03.1 | content 非空非 reasoning-only | G2 + G3 |
| 03.2 | 去 markdown code fence 后 `jq -e .` 不抛(JSON 可解析) | jq |
| 03.3 | keys 含 `meeting` / `date` / `owner` / `result` 全 4 个 | jq has() |
| 03.4 | `meeting == "P1c Smoke"` | 字段精确比 |
| 03.5 | `date == "2026-05-06"` | 同 |
| 03.6 | `owner == "Praise"` | 同 |
| 03.7 | `result == "pass"` | 同 |

任一 fail → case 03 status: fail。

## Case 04 — 长文本分批

| # | assertion | 期望 |
|---|-----------|------|
| 04.1 | content 非空非 reasoning-only | G2 + G3 |
| 04.2 | 字符长度 ∈ [60, 220] | `wc -m` |
| 04.3 | 命中 `P1C_HEALTH` / `P1C_MODELS` / `P1C_ASSERTIONS` 全 3 anchor | grep × 3 |
| 04.4 | 3 anchor 出现顺序与 input 段顺序一致(P1C_HEALTH 在 P1C_MODELS 前,P1C_MODELS 在 P1C_ASSERTIONS 前) | `grep -bo` 取 byte offset 比 |

任一 fail → case 04 status: fail。

## Case 05 — system prompt anchor

| # | assertion | 期望 |
|---|-----------|------|
| 05.1 | content 非空非 reasoning-only | G2 + G3 |
| 05.2 | 末尾(去 trailing whitespace 后)以 `LOCAL_LLM_ANCHOR_OK` 结尾 | bash glob `*LOCAL_LLM_ANCHOR_OK` |
| 05.3 | 含 `readonly` 或 `只读` 至少 1 处 | grep -E |
| 05.4 | 不含 forbidden token: `自动化` / `automation` / `mutation` / `restart` / `pull model`(0 命中) | grep -F × 5 |

任一 fail → case 05 status: fail。

---

## 本轮实测对账(2026-05-07,user 跑 `bash run-smoke.sh --all` + Claude 独立 re-verify)

### Run metadata

| 字段 | 值 |
|------|------|
| Run timestamp(UTC) | 2026-05-07T09:56:10Z(preflight)→ 2026-05-07T09:56:32Z(case 05 完成)|
| Total duration | ~22 秒(5 case 顺序跑) |
| Runner | `Codex/ai-infra/akb2-toolbox-evals/p1c-cc-local-llm/run-smoke.sh` |
| Execution mode | **user-side run**(因 Claude OS-level network sandbox;Bash + curl + jq;无 user 干预 runner 逻辑) |
| Backend base | `http://127.0.0.1:8080` |
| Backend health | HTTP 200 + body `OK` ✓ |
| /v1/models data[] | 6 models:`bge-m3`, `gemma-3-4b`, `nomic-embed`, `qwen2.5-14b`, `qwen3-32b`, `qwen3-8b` |
| Chat fallback available | qwen3-8b ✓ + qwen2.5-14b ✓ + qwen3-32b ✓(3/3 全可用) |
| Chat model used | **qwen3-8b**(preferred,5/5 case 全用,无 fallback 切换) |
| Embedding 误用于 chat? | 0(bge-m3 / nomic-embed 严格隔离,不在 fixture model 字段) |

### 5 case 实测 PASS/FAIL 表

| # | case_id | runner 自评 | Claude 独立 re-verify | content_chars(runner)| 实际 content |
|---|---------|------------|----------------------|----------------------|--------------|
| 1 | 01-summary | **PASS** ✓ | **PASS** ✓ | 39 | `星澜研究院迁移LLM推理入口至llama-swap，采用OpenAI兼容协议。` |
| 2 | 02-classification | **PASS** ✓ | **PASS** ✓ | 4 | `后端契约` |
| 3 | 03-json-extract | **PASS** ✓ | **PASS** ✓ | 83 | `{"meeting": "P1c Smoke", "date": "2026-05-06", "owner": "Praise", "result": "pass"}` |
| 4 | 04-chunked-summary | **PASS** ✓ | **PASS** ✓ | 77 | `健康检查返回OK且延迟22ms，模型列表包含qwen3-8b，5类语义断言全通过。P1C_HEALTH P1C_MODELS P1C_ASSERTIONS` |
| 5 | 05-system-anchor | **PASS** ✓ | **PASS** ✓ | 39 | `已理解约束条件。只读模式下无法修改数据。LOCAL_LLM_ANCHOR_OK` |

**汇总:5/5 PASS**(runner-side + Claude-side 双独立判定一致)。

### 关键 assertion 实测细节(per case)

#### Case 01 — 摘要 ✓
- content 非空非 reasoning-only ✓
- 含 `星澜研究院` ✓ / 含 `llama-swap` ✓ / 不含 `Ollama` 或 `11434` ✓
- 字符长度 = 39 ∈ [12, 60] ✓
- 输出**未泄露**输入中的 legacy token(防输入污染 assertion 守住)

#### Case 02 — 分类 ✓
- content 非空非 reasoning-only ✓
- trim 后 = `后端契约`(精确匹配,无任何额外字符)✓
- 不含 `性能优化` / `安全审计` ✓

#### Case 03 — JSON 抽取 ✓
- content 非空非 reasoning-only ✓
- `jq -e '.'` parse OK ✓(无 markdown code fence 包裹,直接合法 JSON)
- 4 keys 全在:meeting / date / owner / result ✓
- 4 值全精确匹配:`P1c Smoke` / `2026-05-06` / `Praise` / `pass` ✓

#### Case 04 — 长文本分批 ✓
- content 非空非 reasoning-only ✓
- 字符长度 = 77 ∈ [60, 220] ✓
- 3 anchor 全命中 ✓
- byte offset:`P1C_HEALTH=93` < `P1C_MODELS=104` < `P1C_ASSERTIONS=115` ✓(顺序正确,与 input 段顺序一致)

#### Case 05 — system prompt anchor ✓
- content 非空非 reasoning-only ✓
- 末尾(去 trailing whitespace 后):`...LOCAL_LLM_ANCHOR_OK` ✓
- 含 `只读` ✓(中文版本命中,英文 `readonly` 未出现但 OR 关系通)
- 5 forbidden token 全 0 命中:`自动化` / `automation` / `mutation` / `restart` / `pull model` ✓

### Hard fail 触发情况

| 类型 | 触发? | 说明 |
|------|-------|------|
| `/health` 非 200 / body 非 OK | ❌ 未触发 | HTTP 200 + body=OK 实测 |
| `/v1/models` 非 JSON / data[] 空 | ❌ 未触发 | 6 models data[] 完整 |
| 3 chat fallback 全 missing | ❌ 未触发 | 3/3 全可用 |
| 任一 case content empty | ❌ 未触发 | 5/5 case content 全非空 |
| 任一 case reasoning-only | ❌ 未触发 | `/no_think` system prompt 注入有效;`message.content` 全有 visible 内容 |
| 任一 case 语义断言失败 | ❌ 未触发 | 5/5 案断言全 pass |
| embedding 误用于 chat | ❌ 未触发 | fixture 全用 qwen3-8b,bge-m3/nomic-embed 0 调用 |

### 总 verdict

**C-4 L1 smoke verdict:5/5 PASS**(Claude side + runner side 双独立判定一致)。

按 plan v6 §实现步骤 §4 收尾要求:
- 5/5 pass → 进 §5 Review 2(ai_infra)
- Review 2 APPROVED 后 + plan §Review 2 记录 writeback → 进 §6 C-5 Tool Intake Gate
- C-5 verdict 三档 anchor 决策(rollback / patch / accept)

**进入 §5 Review 2**。
