# AKB 本地模型替换方案 v2

> **状态**（2026-04-24 17:30）
> - ✅ Phase 0-4 已完成（router 切到 litellm backend，单测 151/151，端到端翻译 10 条样本质量 OK）
> - ⚠️ **Phase 4 不完整** — v2 review 发现 6 个生产文件还在 instantiate `OllamaClient`，需补做 §3 收口
> - 🔄 Phase 5-B（搬出 iCloud + launchctl 自启）准备执行
> - ⏸ Phase 6/7 等收口完成
> - ⏸ Phase 8 不可逆，等观察期通过

## Summary

目标是把 AKB 从 Ollama 迁移到 `llama.cpp + llama-swap`，但不把"llama-swap 能启动"等同于"AKB 已彻底去 Ollama"。

目标链路：

```text
AKB -> LLMRouter -> LiteLLMClient(OpenAI-compatible) -> llama-swap :8080 -> llama-server -> GGUF
```

执行原则：

- 先让生产管线稳定跑在 llama-swap 上。
- 再收口所有 runtime Ollama 依赖。
- 最后才删除 Ollama 和 `~/.ollama`。
- Phase 8 前不允许生产命令路径里还有 `OllamaClient` / `localhost:11434` / Ollama health check。

## Key Changes

### 1. llama-swap 基础设施（Phase 5-B）

保留原方向，把 llama-swap 资源搬出 iCloud，避免 launchd/TCC 问题。

目标位置：

```text
~/.local/share/akb/bin/llama-swap
~/.local/share/akb/config/llama-swap.yaml
~/.local/share/akb/models/*.gguf
```

`~/Library/LaunchAgents/com.llama-swap.plist` 改为：

- 直接 exec `~/.local/share/akb/bin/llama-swap`
- `-config` 指向 `~/.local/share/akb/config/llama-swap.yaml`
- `-listen 127.0.0.1:8080`
- 不使用 bash launcher
- 不设置 iCloud `WorkingDirectory`
- config 和 model 路径全部使用绝对路径

通过门槛：

```bash
launchctl list com.llama-swap
lsof -iTCP:8080 -sTCP:LISTEN
curl -sS http://127.0.0.1:8080/v1/models
```

### 2. AKB LaunchAgent 可启动性

补上原方案遗漏的 LaunchAgent 修复阶段。

**关键修正**：`bash -c "cd <iCloud_path> && exec <python>"` 中 `cd` 失败会**短路**整个 `&&` 链 — 之前认为"cd 失败但 Python 还能跑"是错的。stderr 里看到的 Python 警告来自旧的成功执行，不是当前失败的执行。

必须修：

- `translate --hours` 从 `type=int` 改为 `type=float`（dry-run 时 `--hours 0.05` / 生产 `--hours 2.5` 都需要）
- 保留 `translator-evening` 的 `--hours 2.5`
- 验证 `com.akb.synthesizer` 确实 loaded 并能跑通完整 import
- 验证 `com.akb.embed` 不再因为 Ollama 或 iCloud cwd 失败

通过门槛：

```text
com.akb.synthesizer        loaded / exit 0
com.akb.translator         loaded / exit 0
com.akb.translator-evening loaded / exit 0
com.akb.embed              loaded / exit 0
```

### 3. Runtime Ollama 依赖收口（**v1 漏掉的关键阶段**）

v1 只改了 `synthesize.py` + `translate.py`，但生产路径还经过下列文件，全部需要 router-first 化：

| 文件 | 当前行为 | 改后行为 |
|---|---|---|
| `scripts/commands/fetch.py:87,367` | 直接 `OllamaClient()` | 用 router；router 不可用时 LLM filter / tool extraction 跳过 |
| `scripts/commands/curator.py` (cmd_curate) | 无条件 `OllamaClient()` + `ensure_running()` | 用 router；`--embed-only` 走 `router.embed()` |
| `scripts/commands/knowledge.py` | 同上检查 | 同上 |
| `scripts/commands/backfill_concepts.py` | Ollama gate | router gate |
| `scripts/pipeline/curator.py:37,56` | 类构造 fallback `OllamaClient` | router-only |
| `scripts/pipeline/tool_extractor.py:47` | `self.llm = llm or OllamaClient()` | `self.llm = llm or router.client`；router 不可用时 ToolExtractor 不构造 |
| `scripts/pipeline/quality_scorer.py:47` | fallback `OllamaClient` | 同上 |
| `scripts/core/search.py` (semantic) | 直接 ollama embed | `router.embed()` |

行为要求：

- `cmd_curate()` 不再无条件创建 `OllamaClient()` 或调用 `ensure_running()`
- `curate --embed-only` 使用 `router.embed()`
- `search --semantic` 使用 `router.embed()`
- `fetch` 用 router availability 决定 arXiv LLM filter / tool extraction 是否启用
- `ToolExtractor` 默认不硬建 `OllamaClient`
- `backfill_concepts` 取消 Ollama gate，改为 router gate
- DB 中 embedding model 记录为当前 E 档模型，例如 `nomic-embed`

`LLMRouter` 和 `LiteLLMClient` 继续复用现有 OpenAI-compatible 路径，MVP 阶段不新增独立 `llama_server` backend。

### 4. 测试与队列口径

**修复当前失败测试**：

- `tests/test_db_pending_filter.py` 的测试 schema 补 `archived_at TEXT`
- 更稳妥做法是改为用 `CatalogDB.init_tables()` 初始化临时 DB

同时**修正 translation backlog 统计口径**（当前 status 报 2648 可能虚高）：

```sql
synthesized_at IS NOT NULL
AND translate_status = 'pending'
AND note_path IS NOT NULL
AND (synth_status = 'ok' OR synth_status IS NULL)
AND archived_at IS NULL
```

不要把尚未 synthesis 的 raw item 算进 translation backlog。

## Test Plan

### 基础测试

```bash
cd "$AKB"
/Users/praise/.pyenv/versions/3.13.12/bin/python3 -m pytest -q tests --disable-warnings --maxfail=1
```

必须通过：

- router per-task override
- pending translation filter
- translate retry/classify tests
- curator/embed 相关 smoke test，如当前没有覆盖则补最小单测

### llama-swap 验证

```bash
curl -sS http://127.0.0.1:8080/v1/models
curl -sS -X POST http://127.0.0.1:8080/v1/chat/completions ...
curl -sS -X POST http://127.0.0.1:8080/v1/embeddings ...
```

必须确认：

- `gemma-3-4b`
- `qwen3-8b`
- `qwen3-32b`
- `nomic-embed`

### AKB 小批量验证

先跑小批量，不直接跑全部积压：

```bash
python3 -m scripts.main translate --hours 1 --force
python3 -m scripts.main synthesize --hours 1 --force
python3 -m scripts.main curate --embed-only
python3 -m scripts.main search "agent memory" --semantic --limit 5
```

通过门槛：

- 不回落 Ollama
- embeddings 成功写入
- semantic search 正常返回或优雅降级
- llama-server 内存峰值小于 18GB
- 没有 `<think>` 泄露到标题、译文标题或文件名
- 20 条样本里 `verified` 不应接近 0；如果仍接近 0，先修 V2 verifier 或降低放量规模

### 三天观察

恢复 LaunchAgents 后观察 3 天：

```bash
launchctl list | grep com.akb
tail -f "$AKB/index/logs/translator-stdout.log"
tail -f "$AKB/index/logs/synthesizer-stdout.log"
tail -f /tmp/llama-swap.out.log
```

进入删除 Ollama 的门槛：

- 0 OOM
- 0 launchd 反复重启
- `translator-evening` exit 0
- `embed` exit 0
- `curator-daily` exit 0
- 无 5 段模板复现
- 无明显翻译质量退化

## Final Cleanup

### Phase 8 显式清理清单（已知 legacy 口径）

主线迁移完成后剩这 4 个文件还有 ollama 痕迹，**Phase 8 删 ~/.ollama 前必须统一处理**：

| 文件 | 现状 | Phase 8 处理 |
|---|---|---|
| `scripts/commands/setup.py:223` | `http://localhost:11434/api/pull` 旧 ollama bootstrap | 改为 llama-swap 健康检查 + 提示用户从 HF 拉 GGUF |
| `scripts/utils/environment.py:90-102` | env probe 仍含 `OllamaClient()` 分支（gated by `AKB_LLM_BACKEND=ollama`） | 删 ollama 分支，仅保留 litellm 探测 |
| `config/.env.example` | 还有 `OLLAMA_HOST=...` / `OLLAMA_MODEL=...` 行 | 删旧两行，加 llama-swap 推荐配置注释 |
| `scripts/utils/llm.py` | 整个 OllamaClient 类（已无生产 instantiate） | 删整个文件 + `scripts/utils/__init__.py:2` 的 re-export |

### Phase 8 验证 gate

```bash
rg -n "OllamaClient|11434|ollama" scripts config tests
```

允许残留：

- 历史文档
- 迁移说明
- 明确标记 legacy 的测试

不允许残留：

- 生产命令路径
- LaunchAgent
- runtime health check
- embedding/search/fetch/curate 路径

删除动作：

```bash
brew uninstall ollama 2>/dev/null || true
rm -rf ~/.ollama
launchctl unsetenv OLLAMA_HOST 2>/dev/null
launchctl unsetenv OLLAMA_MODEL 2>/dev/null
```

同时更新：

- `CLAUDE.md`
- `README.md`
- `config/.env.example`
- Project_AKB skills
- claude-active skills
- `akb-status` / `akb-monitor`

尤其移除旧口径：

```text
localhost:11434/api/tags
qwen2.5:14b
python3 scripts/main.py status --output json
旧 LaunchAgent 时间描述
```

## Assumptions

- MVP 阶段继续使用 `backend=litellm` 指向本地 llama-swap，不新增 `llama_server` backend。
- `translate --hours` 统一改为 `float`，以兼容 `2.5` 和小批量 `0.1` dry-run。
- Phase 8 前保留 `OllamaClient` 类作为 legacy fallback；Phase 8 再删除。
- 不直接运行 `scripts/main.py setup`，因为当前 setup 仍包含旧 Ollama/LaunchAgent 逻辑。
- 不 revert 当前 dirty worktree；现有改动视为 llama-swap 迁移的一部分。

---

## 执行顺序（v1 → v2 增量）

1. **§3 Runtime Ollama 收口**（最关键 — 不做这步，AKB 跑起来仍可能 fallback 到 OllamaClient → Phase 8 删 ollama 后炸）
2. **§4 测试 + backlog SQL 修复**（让 status 数字诚实）
3. **§1 Phase 5-B 搬出 iCloud + launchctl**
4. **§2 AKB plist 验证可启动**
5. **Test Plan 三段验证**
6. **3 天观察**
7. **Final Cleanup（Phase 8）**

§3 和 §4 顺序可互换，§5-B 必须在 §3 之后（先收口 ollama 依赖再切自启）。

---

## 镜像位置

- 主 plan：`/Users/praise/.claude/plans/synthetic-spinning-thacker.md`（本文件）
- iCloud 备份：`/Users/praise/Library/Mobile Documents/com~apple~CloudDocs/Claude Code/plans/akb-llama-swap-migration-v2.md`

两份内容一致。修改时同步更新。
