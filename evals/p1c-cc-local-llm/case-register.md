# P1c C-4 Case Register — cc-local-llm

Phase:P1c L1 smoke
工具:`cc-local-llm`
SKILL.md sha256(P1c-0 后基线):`3a31ebe4f5938864e752ea51d35d89403e62d31a352ac3eaf01a784721c73050`
Backend:`http://127.0.0.1:8080`(llama-swap → llama.cpp / llama-server)
Preferred chat model:`qwen3-8b`
Fallback chat models:`qwen2.5-14b`, `qwen3-32b`
Embedding(不参与 chat smoke):`bge-m3`, `nomic-embed`
Execution mode:**user-side run**(因 Claude OS-level network sandbox 阻 127.0.0.1;Claude 写 runner + fixtures + assertion logic + 读 outputs 比对,user 单条 `bash run-smoke.sh --all`)
Plan ref:`/Users/praise/.claude/plans/joyful-squishing-moonbeam.md` §P1c v6

## 5 case 矩阵

| # | case_id | 用途 | input fixture | output 路径 | assertion 关键 |
|---|---------|------|---------------|-------------|----------------|
| 1 | 01-summary | 摘要 | `fixtures/01-summary/input.json` | `outputs/01-summary/raw-output.json` + `assertion-result.json` | content 非空非 reasoning-only;含 `星澜研究院` + `llama-swap`;长度 ∈ [12, 60];不含 `Ollama` / `11434` |
| 2 | 02-classification | 分类 | `fixtures/02-classification/input.json` | `outputs/02-classification/...` | content 精确 = `后端契约`(trim);不含其它候选 `性能优化` / `安全审计` |
| 3 | 03-json-extract | JSON 抽取 | `fixtures/03-json-extract/input.json` | `outputs/03-json-extract/...` | `JSON.parse` OK;keys = {meeting, date, owner, result};值精确 `P1c Smoke` / `2026-05-06` / `Praise` / `pass` |
| 4 | 04-chunked-summary | 长文本分批 | `fixtures/04-chunked-summary/input.json` | `outputs/04-chunked-summary/...` | content 非空非 reasoning-only;长度 ∈ [60, 220];命中 `P1C_HEALTH` / `P1C_MODELS` / `P1C_ASSERTIONS` 全 3 anchor;顺序与 input 一致 |
| 5 | 05-system-anchor | system prompt anchor | `fixtures/05-system-anchor/input.json` | `outputs/05-system-anchor/...` | 末尾(去 trailing whitespace)以 `LOCAL_LLM_ANCHOR_OK` 结尾;含 `readonly` 或 `只读`;不含 5 forbidden token(`自动化` / `automation` / `mutation` / `restart` / `pull model`)|

## Hard fail 触发(任一 → C-4 fail,不进 §5 Review 2)

- `/health` 非 200 / body 非 `OK`
- `/v1/models` 不可解析 / `data[]` 空
- 3 chat fallback(qwen3-8b / qwen2.5-14b / qwen3-32b)全 missing
- 任一 case `message.content` empty
- 任一 case reasoning-only(`content` empty 但 `reasoning_content` 非空)
- 任一 case 语义断言失败
- embedding 模型(bge-m3 / nomic-embed)被误用于 chat smoke(本 fixture 全用 qwen3-8b,理论不会触发)

## 不允许 fallback 覆盖语义失败

per plan v6 §接口与数据合约:**preferred chat model 可用但语义断言失败 → C-4 fail,不得 fallback 掩盖**。所有 5 case fixture 都硬编码 `model: "qwen3-8b"`,不在 runner 内自动切 fallback。fallback 只在 preflight 检测 qwen3-8b missing 时才考虑,且需要 user 决策。

## 评估流程

1. user 跑 `bash Codex/ai-infra/akb2-toolbox-evals/p1c-cc-local-llm/run-smoke.sh --all`
2. runner 输出 outputs/preflight.json + 5 个 outputs/<case>/raw-output.json + 5 个 outputs/<case>/assertion-result.json
3. user 通知 Claude run 完(贴 stdout 摘要或仅 "done")
4. Claude 读 outputs/ 全部 JSON,核对 assertions.md(待写),写 run-evidence.md
5. 5/5 pass → §5 Review 2;任一 fail → 按 plan v6 §verdict anchor 决策(rollback / patch / accept)
