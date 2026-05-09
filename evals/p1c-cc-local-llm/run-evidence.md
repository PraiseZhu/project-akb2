# P1c C-4 L1 Smoke Run Evidence — cc-local-llm

## 元数据

| 字段 | 值 |
|------|------|
| Phase | P1c L1 smoke(C-4) |
| Tool | `cc-local-llm` |
| Workspace | `~/.codex/skills/cc-local-llm/SKILL.md` sha256 = `3a31ebe4f5938864e752ea51d35d89403e62d31a352ac3eaf01a784721c73050`(P1c-0 后基线;`b8050d3c236a` → `3a31ebe4f593`)|
| Backend | `http://127.0.0.1:8080`(llama-swap → llama.cpp / llama-server)|
| Backend health | HTTP 200 + body `OK` ✓ |
| Models in `/v1/models` | 6:`bge-m3`, `gemma-3-4b`, `nomic-embed`, `qwen2.5-14b`, `qwen3-32b`, `qwen3-8b` |
| Chat fallback 全可用 | qwen3-8b ✓ + qwen2.5-14b ✓ + qwen3-32b ✓ |
| Chat model used | **qwen3-8b**(preferred,5/5 case 全用)|
| Embedding 是否参与 chat smoke | ❌ 0(`bge-m3` / `nomic-embed` 严格隔离) |
| **Execution mode** | **user-side run**(因 Claude OS-level network sandbox 阻 127.0.0.1;`--dangerously-skip-permissions` 与 `dangerouslyDisableSandbox: true` 实测均无法 bypass;Claude 写 runner + fixtures + 读 outputs,user 跑 1 命令) |
| Run started(UTC)| 2026-05-07T09:56:10Z |
| Run finished(UTC)| 2026-05-07T09:56:32Z |
| Total duration | ~22 秒 |
| Plan ref | `/Users/praise/.claude/plans/joyful-squishing-moonbeam.md`(plan v6 §测试计划 §C-4 fixed fixtures + §User Override A 路径)|

## SKILL.md / 4 对照 SKILL.md 不变性证据

```bash
shasum -a 256 ~/.codex/skills/cc-local-llm/SKILL.md
# 期望: 3a31ebe4f5938864e752ea51d35d89403e62d31a352ac3eaf01a784721c73050(P1c-0 后)

# 4 对照 byte-identical(P1c 全程必须保持):
shasum -a 256 ~/.codex/skills/cc-{executable-plan,review,akb-search,skill-stocktake}/SKILL.md
# 期望:
#   cc-executable-plan: 5af73fdadd91097cc60f8e6c3622427aeaac8382bda0d1525debe564f2c46d0a
#   cc-review:          592cf3ed4b2c6e3f96c63a500bdbe3bebbc375a19983debbfa7f1877f406d274
#   cc-akb-search:      b669380de8a0cd4319f0c978f4cfa69d2757b004a8569fc3adac8f633637f982
#   cc-skill-stocktake: 52d10db4618d540915c5e5e6053dcce51c4ca98d4285c829d88b9d832f9c8fb6
```

预期:5/5 byte-identical(C-5 hard check #4 守住)。

## 5 case 执行表

| # | case_id | input.json | raw-output.json | assertion-result.json | runner verdict | Claude re-verify | content_chars | content 摘要 |
|---|---------|-----------|-----------------|----------------------|---------------|------------------|---------------|--------------|
| 1 | 01-summary | `fixtures/01-summary/input.json`(523b)| `outputs/01-summary/raw-output.json` | `outputs/01-summary/assertion-result.json` | **PASS** | **PASS** | 39 | "星澜研究院迁移LLM推理入口至llama-swap..." |
| 2 | 02-classification | `fixtures/02-classification/input.json`(452b)| `outputs/02-classification/raw-output.json` | `outputs/02-classification/assertion-result.json` | **PASS** | **PASS** | 4 | "后端契约" |
| 3 | 03-json-extract | `fixtures/03-json-extract/input.json`(458b)| `outputs/03-json-extract/raw-output.json` | `outputs/03-json-extract/assertion-result.json` | **PASS** | **PASS** | 83 | `{"meeting": "P1c Smoke", "date": "2026-05-06", "owner": "Praise", "result": "pass"}` |
| 4 | 04-chunked-summary | `fixtures/04-chunked-summary/input.json`(726b)| `outputs/04-chunked-summary/raw-output.json` | `outputs/04-chunked-summary/assertion-result.json` | **PASS** | **PASS** | 77 | "...P1C_HEALTH P1C_MODELS P1C_ASSERTIONS" |
| 5 | 05-system-anchor | `fixtures/05-system-anchor/input.json`(546b)| `outputs/05-system-anchor/raw-output.json` | `outputs/05-system-anchor/assertion-result.json` | **PASS** | **PASS** | 39 | "已理解约束条件。只读模式下无法修改数据。LOCAL_LLM_ANCHOR_OK" |

**汇总:5/5 case PASS**(runner-side 自评 + Claude-side 独立 re-verify 双判定一致)。

## 关键 hard fail 防护实测(全未触发)

| 防护 | 触发? |
|------|-------|
| `/health` 非 200 / body 非 OK → C-4 hard fail | ❌(实测 200 + OK)|
| `/v1/models` 不可解析 / `data[]` 空 | ❌(实测 6 models)|
| 3 chat fallback 全 missing | ❌(实测 3/3 全可用)|
| 任一 case content empty | ❌(5/5 全非空)|
| 任一 case reasoning-only(`content` 空但 `reasoning_content` 非空) | ❌(`/no_think` 系统 prompt 有效,5/5 case `message.content` 全 visible)|
| 任一 case 语义断言失败 | ❌(5/5 全 pass)|
| embedding 误用于 chat | ❌(0 调用 bge-m3 / nomic-embed)|
| fallback model 切换覆盖语义失败(违反 plan v6 §接口) | ❌(0 切换;5/5 case 全用 preferred qwen3-8b)|

## reviewer 复测命令

```bash
DST="/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Codex/ai-infra/akb2-toolbox-evals/p1c-cc-local-llm"

# 1. preflight evidence
jq . "$DST/outputs/preflight.json"
# 期望: health.status=pass, chat_models_available 含 qwen3-8b

# 2. 5 case verdict 实测对账
for c in 01-summary 02-classification 03-json-extract 04-chunked-summary 05-system-anchor; do
  printf "%-25s status=%s reason=%s\n" "$c" \
    "$(jq -r '.status' "$DST/outputs/$c/assertion-result.json")" \
    "$(jq -r '.reason' "$DST/outputs/$c/assertion-result.json")"
done
# 期望:
#   01-summary               status=pass reason=all_assertions_pass
#   02-classification        status=pass reason=all_assertions_pass
#   03-json-extract          status=pass reason=all_assertions_pass
#   04-chunked-summary       status=pass reason=all_assertions_pass
#   05-system-anchor         status=pass reason=all_assertions_pass

# 3. 5 case content 实测内容
for c in 01-summary 02-classification 03-json-extract 04-chunked-summary 05-system-anchor; do
  echo "--- $c ---"
  jq -r '.choices[0].message.content' "$DST/outputs/$c/raw-output.json"
done

# 4. 重跑某 case 单独
bash "$DST/run-smoke.sh" --case 01

# 5. SKILL.md hash chain 一致性
shasum -a 256 ~/.codex/skills/cc-local-llm/SKILL.md
grep "workspace_version" "/Users/praise/Library/Mobile Documents/iCloud~md~obsidian/Documents/Praise Space/AKB2.0/toolbox/interfaces/cc-local-llm.md"
# 期望: shasum 输出前 12 char = frontmatter workspace_version 字段值前 12 char(3a31ebe4f593)

# 6. 4 对照 SKILL.md byte-identical
for slug in cc-executable-plan cc-review cc-akb-search cc-skill-stocktake; do
  shasum -a 256 ~/.codex/skills/$slug/SKILL.md | cut -d' ' -f1
done
# 期望与 case-register.md / plan §当前状态 baseline 表 4 行一致
```

## 评估结论

- **5/5 case PASS**(双独立判定一致)
- **0 hard fail 触发**
- **runner-side / Claude-side / preflight evidence 三层证据齐**
- runner 在 `outputs/` 落 5 个 raw-output.json + 5 个 assertion-result.json + 1 个 preflight.json(共 11 文件),reviewer 可独立复跑
- `~/.codex/skills/cc-local-llm/SKILL.md` 仍是 P1c-0 后基线 `3a31ebe4f593`(C-4 不修改 SKILL.md)
- 4 对照 SKILL.md 仍 byte-identical(P1c 全程必须保持)

## 升级提议

- **eval_level: L0 → L1**:5/5 case + preflight + assertion 实测充分(非 fallback 覆盖 / 非 reasoning-only / 非 empty)
- **status: candidate → active**:**待 §6 C-5 Tool Intake Gate 9 项 hard checks 全 pass 后才升**(本 §4 不动 status)
- **不升 L2**:per user 硬边界 #4 + plan v6 §非目标
- **automation_allowed: false** 全程保持
- **runtime_mutation_allowed: false** 全程保持

## 链接

- correction report:`AKB2.0/docs/phase-c-reports/p1c-local-llm-backend-contract-refresh-2026-05-07.md`
- case-register:`./case-register.md`
- assertions(含本轮实测对账):`./assertions.md`
- run-smoke.sh runner:`./run-smoke.sh`
- 5 fixture input.json:`./fixtures/{01-summary, ..., 05-system-anchor}/input.json`
- 5 raw-output.json + 5 assertion-result.json + 1 preflight.json:`./outputs/`
- plan v6:`/Users/praise/.claude/plans/joyful-squishing-moonbeam.md`(本 evidence 写到 plan §Review 2 记录后,§C-5 记录 决策时引用)
