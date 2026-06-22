# AKB2 内容质量循环收尾 P0-P2 计划（CQL Back-Half）

GD_STANDARD: Project GD/prompts/gd-review-standard.md
TEMPLATE_KIND: gd-plan

> 作者：Claude（Sonnet 4.6）
> 日期：2026-06-15
> 状态：draft
> 背景：本会话 G1-G3 已完成并提交（commit `3cae2c3` defuddle 调用修复 / `7cd6bfc` scrapling→defuddle 转交 + author 可归属主体兜底）；intake 层 archived 从 0→4（openai Cloudflare 文章实测入库，raw 文件 9 字段 frontmatter + §A-§R 锚点齐）。

---

## Review 对齐

- REVIEW_DOMAIN：`ai_infra`
- REVIEW_FOCUS: `全链七段 fail-closed 段间贯穿(后半段首次真跑); 真 API 预算具名与失败可见; 校准语料可达性 + fetch 路径与生产一致; maker/checker 异族分离(deepseek/gpt-5.4/gemini-flash); 测试隔离(不写真 vault/不污染 ledger)`

---

## 目标链（Goal Chain）

```
PROJECT_GOAL: AKB2 内容质量循环(循环1)全链上线,以 76 个源自动供料,产出与黄金样张同级的入库内容
CHAIN_GOAL:   intake 已出货(archived>0),但准入→提炼→评分→升格后半段从未用真实文章端到端跑过,循环是否端到端贯通无任何实测证据
PHASE_GOAL:   后半段全链对真实文章有实测结论(每篇 final_status + 卡点);校准成为可信测量工具(语料全可达 + fetch 走生产路径);源可达≥70
TASK_GOAL:    runner --once 对 4 篇真实 raw 跑完后半段产 quality-ledger.jsonl,每篇 final_status 与卡点段可读
```

**说明**：
- intake 出货是本会话已验证的事实（archived=4），但那是七段中的前段（discovery→extract→archive）。
- 后半段（admission→distill→grade→repair→promote）需真 API（gemini-flash/deepseek/gpt-5.4），从未真跑。
- spec（`plans/gd/2026-06-12-content-quality-loop/master-plan.md`）核心验收门 SC-3「≥90% 正样本 ≤3 修复轮走完全链」当前实测 0%，但已查明 0% 主因是 defuddle 调用 bug（已修）+ 校准语料 14/18 是虚构 URL（本计划 P1 处理），非管线本身。

---

## 非目标（Non-Goals）

- 不做 launchd 加载 / Mac mini 部署 — 属循环全通后的 P3，且新增自动化触发器必须人工确认（constitution 自动化纪律），agent 不自行注册。
- 不做 vault 修宪 + 开启 auto-promote — P0 全程用 proposal 模式（`AKB2_AUTO_PROMOTE=0`），不写真 vault；修宪是出厂序列，本计划不触及。
- 不改 `grader/repair/promote/runner` 的判定逻辑 — 除非 P0 实测暴露 bug，否则下游模块只读跑；暴露 bug 即停下单独决策。
- 不为抬高通过率降低黄金样张/量规标准 — constitution：校准死锁时区分"管线缺陷"vs"语料不当"，卡点如实报告，不降量规。
- 不在语料重建时塞入未经验证的 URL — 每条入选前必须实测可达（避免重蹈原语料"编造看似合理 URL"的覆辙）。

---

## 成功标准（SC）

- [ ] SC-1：4 篇真实 raw 经 `runner --once` 跑完后半段，quality-ledger 记录每篇到达的最远段 + final_status
  - verify (method: command): `AKB2_AUTO_PROMOTE=0 PYTHONPATH=src python -m akb2.quality.runner --once --raw-dir /tmp/akb2-cql-p0/raw --vault-root /tmp/akb2-cql-p0/vault --staging-dir /tmp/akb2-cql-p0/staging`（逐篇，**必带 `--staging-dir`，R2.1 修复**）+ 读 `/tmp/akb2-cql-p0/staging/quality-ledger.jsonl`
  - **约束**：SC-1 禁止使用不带 `--staging-dir` 的 runner 命令（无之则 staging 落真 vault，违反隔离）
  - expect: 每篇有终态（promoted/proposal_passed/rejected/failed），进程非崩溃；卡点段可定位
- [ ] SC-2：未走完的篇目明确卡在哪段（admission/distill/grade/repair/promote）及原因
  - verify (method: assertion): ledger 中每篇 final_status≠promoted 的都有对应 `abort` event 且 `node`+`reason` 非空
  - expect: 卡点分布表，每条非通过项有 node+reason
- [ ] SC-3：corpus.yaml 20 条 URL 全部实测可达（正样本 defuddle parse 出真内容 ≥200 词；2 负样本保留）
  - verify (method: command): 逐 URL `defuddle parse <url>` + 词数断言；`grep -c 'expected: ingest' corpus.yaml`
  - expect: 18 正样本词数≥200，2 负样本保留，0 个 404
- [ ] SC-4：`calibration._fetch_url_content` 改用 `extract_content`（生产决策树），Cloudflare 源经 scrapling→defuddle 可抓
  - verify (method: test): `python -m pytest tests/quality/ -q -k "calibration or fetch"` + 对 openai URL 断言校准内 fetch 成功
  - expect: exit 0；openai 类源在校准中 fetch 成功（此前 defuddle 旁路恒失败）
- [ ] SC-5：升级后 source-ledger `reachable + needs_browser` ≥70/76
  - verify (method: command): `AKB2_LIVE_VALIDATE=1 PYTHONPATH=src python -m akb2.quality.intake.source_validator --live --out reports/source-validation/sources-ledger.jsonl` + 断言 reachable+needs_browser≥70
  - expect: ≥70
- [ ] SC-6：全量 quality+loops 测试 + lint 绿
  - verify (method: command): `python -m pytest tests/quality/ tests/loops/ -q -m "not integration" && ruff check src/akb2/quality/`
  - expect: PASS（integration deselected）
- [ ] SC-7（R1 修复 · staging 隔离）：runner 支持 `--staging-dir`，P0 运行时 resolved `ledger_path` 必须落在 `/tmp/akb2-cql-p0/` 下，真实 staging/ledger 零写入
  - verify (method: command): 跑 P0.3 前先记录真 staging 的 `quality-ledger.jsonl`/`grader-results/`/`failures.jsonl` 的 mtime+sha256（before 快照）；加 `--staging-dir /tmp/akb2-cql-p0/staging` 跑完 runner --once 后比对 after：`test -f /tmp/akb2-cql-p0/staging/quality-ledger.jsonl`（隔离 ledger 已生成）+ 真 staging before==after（mtime/hash 不变，不依赖真 vault 是否 git repo —— R2 残余修复）
  - expect: `STAGING_ISOLATED`；真 staging/ledger 无 P0 事件
- [ ] SC-8（R3 修复 · 路由+预算可验收）：P0.2 打印各段 provider/model 且 maker/checker 异族；声明具名预算上限
  - verify (method: command): P0.2 输出 `admission=gemini-flash distill=deepseek grade=gpt-5.4` + 断言三族不同 + `MAX_ARTICLES=4/MAX_REPAIR_ROUNDS=3/MAX_API_CALLS=60`
  - expect: `ROUTES_OK` + `BUDGET_OK`；SC-1/SC-2 卡点分析含 provider/model/call_count 字段

---

## 实施步骤

```text
优先级映射：P0 = Step P0.0~P0.4（SC-1,SC-2,SC-7,SC-8）；P1 = Step P1.1~P1.2（SC-3,SC-4）；P2 = Step P2.1~P2.3（SC-5）
依赖：P0.0(staging 隔离参数) 是 P0.3 的硬前置；P0 不依赖 P1/P2，可立即开（需真 API key）；P2.3(SC-9) 依赖 P1 完成
```

### Step P0.0：为 runner 加 `--staging-dir` 隔离参数（R1 修复 · staging 隔离前置）`[SC-7]`

WHERE: `src/akb2/quality/runner.py` 的 `main()`（argparse 段 ~line 851-873 + RunnerConfig 构造 ~line 907-916）

WHAT: 新增 `--staging-dir` CLI flag；main() 中 `staging_dir` 解析顺序改为 `args.staging_dir or get_staging_root()`（显式传入优先于 vault.toml 解析）。**只加隔离参数，不动任何段判定逻辑（admission/distill/grade/promote）。**

WHY: 审查 R1[P1] 实测确认：当前 `runner.py:894-900` 用 `get_staging_root()` 读固定 `config/vault.toml`，`--vault-root` 不改 staging；`src/akb2/loops/paths.py:_read_vault_config` 读死路径无 env override。**不加此参数则 P0 真 API 跑出的 quality-ledger/grader-results/failures 会写进真实 staging，污染生产 ledger** —— 违反本计划核心边界。

VERIFY（CLI 解析级，**不依赖 raw/ledger 存在 —— R2.2 修复顺序问题**）：构造 RunnerConfig 或调 main 的 arg 解析，打印 resolved `staging_dir`，断言 `== /tmp/akb2-cql-p0/staging`：
```bash
PYTHONPATH=src python3 -c "
import sys; sys.argv=['runner','--once','--raw-dir','/tmp/akb2-cql-p0/raw','--vault-root','/tmp/akb2-cql-p0/vault','--staging-dir','/tmp/akb2-cql-p0/staging']
# 解析 args + 构造 config（不进 _run_main_loop），打印 config.staging_dir / config.ledger_path
from src.akb2.quality import runner as R
# 期望 config.ledger_path 落在 /tmp/akb2-cql-p0/staging/quality-ledger.jsonl
print('STAGING_RESOLVED_OK')
"
```
（ledger **实际生成**的验证留到 P0.3，那时 raw 已就位）

**Hard-stop**：resolved `staging_dir`/`ledger_path` 仍不在 `/tmp/akb2-cql-p0/` 下 → 停止，禁止跑 P0.3（会污染真 ledger）

---

### Step P0.1：准备隔离测试环境 `[SC-1]`

WHERE: 新建 `/tmp/akb2-cql-p0/{raw,vault,staging}/`（系统 /tmp，**仓库外**，不触碰项目 git 状态 —— R2 修复，避开 `.gitignore` 未含 `_tmp/` 的问题）

WHAT: 把已抓的 4 篇 raw 复制到 `/tmp/akb2-cql-p0/raw/`。源文件持久绝对路径：`/Users/praise/.claude/jobs/3469d175/tmp/live2/raw/openai-blog--*--raw--2026-06-15.md`（已确认跨会话存活）。**若该路径已被清理**：用 `AKB2_LIVE_VALIDATE=1 PYTHONPATH=src python -m akb2.quality.intake.orchestrator --limit 4 --sources-config config/main_sources.yaml --state-file /tmp/akb2-cql-p0/state.jsonl --output-dir /tmp/akb2-cql-p0/raw` 重新生成

WHY: runner 默认写真 vault/staging，必须隔离防污染真 vault

VERIFY: `ls /tmp/akb2-cql-p0/raw/*.md | wc -l` → `4`

**Hard-stop**：`--raw-dir` / `--vault-root` / `--staging-dir` 任一指向真 vault/staging 路径时停止 → 会污染知识库

---

### Step P0.2：确认真 API key + 路由/预算 gate `[SC-1, SC-8]`

WHERE: 环境变量 `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY`（llm_router 读取，可选 `*_BASE_URL`/`*_MODEL_ID` 走代理网关）

WHAT:
1. **key + 网关配置（本轮实测确定 · 关键补丁）**：环境只有一把 tapsvc 网关 key（`DEEPSEEK_API_KEY` 值，OpenAI 兼容网关 `llm-proxy.tapsvc.com`）。`OPENAI_API_KEY` 现存值是失效的另一把（401）。**llm_router 默认 model id / base_url 与网关对不上**，跑前必须用以下 env override（实测三家全通）：
   ```bash
   export GW_KEY="$DEEPSEEK_API_KEY"      # 统一网关 key
   export DEEPSEEK_BASE_URL=https://llm-proxy.tapsvc.com/v1   # 必带 /v1，否则 404
   export DEEPSEEK_MODEL_ID=deepseek/deepseek-v4-pro          # 网关要 deepseek/ 前缀
   export OPENAI_API_KEY="$GW_KEY"        # 覆盖失效值
   export OPENAI_BASE_URL=https://llm-proxy.tapsvc.com/v1
   export OPENAI_MODEL_ID=gpt-5.4
   export GEMINI_API_KEY="$GW_KEY"        # 原本为空
   export GEMINI_BASE_URL=https://llm-proxy.tapsvc.com/v1
   export GEMINI_MODEL_ID=gemini-3.5-flash                    # 非 -preview（守 P7）
   ```
   持久化方式：P0 用上述**内联 export**（key 已在 shell env，不新增 secret 文件）；不改 llm_router 默认值（避免动生产默认）。
2. **路由检查（R3 修复 · maker/checker 异族分离）**：打印各段 provider/model —— admission→gemini-flash、distill→deepseek、grade→gpt-5.4；断言三者非同族（maker=deepseek / checker=gpt / fallback=gemini，constitution P7；共用网关但模型家族不同，P7 成立）。
3. **预算 gate（R3 修复 · 真 API 预算具名）**：声明本轮具名上限 `MAX_ARTICLES=4` / `MAX_REPAIR_ROUNDS=3` / `MAX_API_CALLS=60`；runner 跑超限须 fail-closed 并写卡点报告。

WHY: 后半段全需真 API；审查 R3[P2] 指出 REVIEW_FOCUS 声明的"预算具名 + maker/checker 分离"在 SC/Step 无可执行断言。本轮实测发现 key 没问题（用户早已给），但 llm_router 默认 model id（缺 `deepseek/` 前缀、`gemini-2.5-flash` 网关无）+ base_url（缺 `/v1`、openai/gemini 未指网关）+ OPENAI_API_KEY 失效值导致三家全 fail，必须 env override 矫正。

VERIFY:
- key+路由: 带上方 env override 跑 `route_llm` 对 deepseek-v4-pro/gpt-5.4/gemini-flash 各发 1 句 → 三家均返回非空（实测已通过）→ `ROUTES_OK`
- 预算: 声明 `MAX_ARTICLES=4 MAX_REPAIR_ROUNDS=3 MAX_API_CALLS=60` → `BUDGET_OK`

**Hard-stop**：未带 env override 直接跑（deepseek 404 / openai 401 / gemini 空）→ 停止矫正。路由出现同族 maker/checker（如 grade 也走 deepseek）→ 停止（违反 P7）。

---

### Step P0.3：逐篇跑后半段（proposal 模式） `[SC-1, SC-2]`

WHERE: `python -m akb2.quality.runner --once`

WHAT: `AKB2_AUTO_PROMOTE=0` 下对 4 篇逐个执行 `--once --raw-dir /tmp/akb2-cql-p0/raw --vault-root /tmp/akb2-cql-p0/vault --staging-dir /tmp/akb2-cql-p0/staging`（P0.0 新增的隔离参数）；每跑一篇读 `/tmp/akb2-cql-p0/staging/quality-ledger.jsonl` 的新增事件

WHY: 后半段（准入→提炼→评分→修复→升格）从未真跑；proposal 模式不写真 vault 但完整跑七段链；Gate-5 已在本会话 P1 接通（grader 结果落盘传入）

VERIFY:
- **跑前硬前置（R1 修复）**：`PYTHONPATH=src python3 -c "from src.akb2.quality.runner import RunnerConfig; ... print(resolved staging_dir)"` 或运行时打印 `config.ledger_path`，断言落在 `/tmp/akb2-cql-p0/**/quality-ledger.jsonl` 才放行
- ledger 出现每篇的 `admission`/`distill_*`/`grade_*`/`repair_loop`/`promote` 或 `abort` 事件

**Hard-stop**：
- **resolved `ledger_path` 不在 `/tmp/akb2-cql-p0/` 下 → 立即停止，禁止跑 runner（会污染真 ledger，R1 核心防护）**
- 某篇 runner 进程崩溃（非 fail-closed 的 abort event）→ 停下查异常堆栈，可能是下游模块 bug

---

### Step P0.4：汇总卡点 + 通过率 `[SC-2]`

WHERE: 分析 `/tmp/akb2-cql-p0/staging/quality-ledger.jsonl`

WHAT: 统计 4 篇各自 final_status；对未 promoted 的列出 abort `node` + `reason`；算 proposal_passed 率

WHY: 回答 spec SC-3 的核心问题"循环能不能端到端跑通"，并定位下一堵墙

VERIFY: 输出 4 篇 final_status 表 + 卡点段分布

**Hard-stop**：4 篇全卡同一段 → 那段是下一堵墙，停下专项诊断（如全卡 distill = deepseek 提炼有问题）

---

### Step P1.1：重建校准语料 `[SC-3]`

WHERE: `evals/main-pipeline-quality/corpus.yaml`

WHAT: 替换 14 个 404/虚构 URL；每条新 URL 入选前用 `defuddle parse <url>` 验证 ≥200 词真内容；保留已验证可抓的真黄金样张（addyo loop-engineering 2473w）+ aider.chat + mcp.io + 2 负样本；维持 18 正（领域配比：AI Agent 4 / Loop Engineering 3 / Context Engineering 2 / Harness 2 / Multi-Agent 2 / 其余 5）+ 2 负

WHY: 原语料 14/18 是当初编造/陈旧的 URL（claude-code 仓库公开但 docs 路径 404），校准量永远 0% 且无法区分管线 vs 语料问题

VERIFY: 逐 URL `defuddle parse` 词数断言 + `grep -c 'expected: ingest'` = 18 + 无 404

**Hard-stop**：找不到足够真实可达的领域配比 URL → 记录缺口，宁可少几条也不用编的 URL 凑数

---

### Step P1.2：校准 fetch 接通生产路径 `[SC-4]`

WHERE: `src/akb2/quality/calibration.py` 的 `_fetch_url_content`

WHAT: 改为调用 `extract_content(url, extraction_config=...)`（生产决策树，含 G2 的 scrapling→defuddle + G3 author 兜底），取 `result.content`/元数据；删除独立的 `defuddle parse <url>` + urllib 旁路

WHY: 校准旁路不走生产抓取路径，G2 对 Cloudflare 源的改善校准测不到，校准与生产脱节 → 测量结果不反映真实管线能力

VERIFY: `python -m pytest tests/quality/` 相关测试绿 + 对 openai URL 断言校准内 fetch 成功

**Hard-stop**：`extract_content` 返回结构与校准期望的 content 格式不兼容 → 写适配层，不改 extract_content 契约

---

### Step P2.1：源尾巴处理 `[SC-5]`

WHERE: `config/main_sources.yaml` + `src/akb2/quality/intake/source_validator.py`

WHAT: 3 个硬死源（eric-elliott 402 / meta-ai 400 / shadcn 404）换有效链或标记淘汰；3 个间歇源（netflix/ryan-carniato/mistral，连接层波动）加 listing 兜底或标 `browser_needed`

WHY: 当前 reachable+browser=70 卡在 ≥70 阈值线（httpx 直达 69），3 间歇源波动影响稳定性

VERIFY: 重跑 source_validator，`reachable + needs_browser ≥ 70`

**Hard-stop**：换链后仍 <70 → 记录真实死源数，不强凑（宪法 SC-8 缺口由后续挖掘批次补）

---

### Step P2.2：窄工具缺口 `[SC-5]`

WHERE: `src/akb2/quality/intake/extract.py`（defuddle size 上限处理）+ 源处理

WHAT: crewai（6MB 超 defuddle 5MB 上限）→ 提高上限或正文截断；material.io（JS 站，defuddle 无内容）→ 走 scrapling browser→defuddle；github（偶发 429）→ 加 backoff 重试

WHY: 这几类源现有工具链抓不到，属真实工具缺口（区别于虚构 URL）

VERIFY: 对应源 `extract_content` 返回非空 content

**Hard-stop**：无（best-effort，失败的标记并跳过）

---

### Step P2.3：SC-9 真实源专项 `[SC-1 复用]`

WHERE: `python -m akb2.quality.calibration --live-sample 3`

WHAT: 修完 P1 后跑 SC-9（3 个真实 reachable 源各抓 1 篇新内容跑全链，测达黄金样张所需修复轮数）；P0 已部分覆盖此意图

WHY: **外部 spec SC-9**（指 `plans/gd/2026-06-12-content-quality-loop` 的 SC-9，非本计划 —— 本计划只有 SC-1~SC-8）从未跑过，`live-sample.json` 不存在

VERIFY: `reports/calibration/live-sample.json` 产出，3 篇有 final_status + repair_rounds

**Hard-stop**：无

---

## 边界约束

### 文件写入范围

**允许写入**（仅限）：
- `src/akb2/quality/calibration.py`、`src/akb2/quality/intake/extract.py`
- `src/akb2/quality/runner.py` —— **仅限 P0.0 的 `--staging-dir` CLI flag + staging_dir 解析顺序**（R1 修复豁免，不动段判定逻辑）
- `config/main_sources.yaml`、`evals/main-pipeline-quality/corpus.yaml`
- `tests/quality/`、`reports/calibration/`、`reports/source-validation/`
- `/tmp/akb2-cql-p0/`（系统 /tmp 临时测试环境，仓库外，不入 git）

**绝对禁止写入**：
- 真 vault 任何文件（P0 全程用隔离 `/tmp/akb2-cql-p0/vault`）
- `src/akb2/quality/{grader,repair,promote}.py` 的判定逻辑（除非 P0 暴露 bug，暴露即停下决策）
- `src/akb2/quality/runner.py` 的**段判定逻辑**（admission/distill/grade/promote 调用与七段编排）—— **例外（R1 修复豁免）**：Step P0.0 允许只新增 `--staging-dir` CLI flag + staging_dir 解析顺序（测试隔离 enabler，不改任何段行为）
- `~/.claude/**`、launchd plist 的加载、`.env`/`*.key` 等敏感文件
- `schema/`、`plans/gd/2026-06-12-*` 与 `2026-06-14-*`（旧计划只读）

### 复用 vs 绕开

| 复用 | 不复用 |
|------|--------|
| `runner --once`（现成单篇调试入口）、`extract_content`（生产抓取决策树）、`source_validator`（本会话已升级的抓取阶梯）、已抓的 4 篇 raw | 不重写 runner 全链；不绕开 extract_content 另写抓取；不用 fixture 替代真 API |

---

## 依赖与前置条件

- G1-G3 已提交：commit `3cae2c3`（defuddle 调用修复）+ `7cd6bfc`（scrapling→defuddle + author 兜底）
- 真 API key：`DEEPSEEK_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY`（P0 硬前置；可走 `*_BASE_URL` 代理网关）
- 4 篇 raw 文件：持久绝对路径 `/Users/praise/.claude/jobs/3469d175/tmp/live2/raw/`（已确认跨会话存活），需复制到 `/tmp/akb2-cql-p0/raw/`；若已清理见 P0.1 重生成兜底命令
- 抽取工具：defuddle CLI（v0.17.0，已装）、scrapling、docling（已装）

---

## 风险与防护

| 风险 | 防护 |
|------|------|
| runner 写真 vault 污染知识库 | P0 用隔离 `/tmp/akb2-cql-p0/vault` + `AKB2_AUTO_PROMOTE=0`（proposal 模式不写 active） |
| 真 API 花费失控 | P0 只 4 篇 + proposal 模式；预算具名；跑前确认 key（Step P0.2 硬门） |
| 语料重建塞入编造 URL（重蹈覆辙） | 每条入选前 `defuddle parse` 实测验证（SC-3 钉死），找不到就少几条 |
| 校准接通 extract 破坏现有校准测试 | Step P1.2 先跑测试，格式差异用适配层兜底，不改 extract_content 契约 |
| P0 暴露 grader/promote bug 但属 forbidden 路径 | 暴露即停下报告，单独决策是否解禁修，不在本计划内擅自改下游判定 |
| 后半段全卡某一段（如 distill）伪装成"循环不通" | Step P0.4 区分"段缺陷"vs"内容/语料不当"，卡点如实报告不降标准 |

---

## 交付物清单

| 文件 | 类型 | SC 映射 | 验收状态 |
|------|------|---------|---------|
| `src/akb2/quality/runner.py`（仅 `--staging-dir` flag） | modified | SC-7 | [ ] |
| `/tmp/akb2-cql-p0/staging/quality-ledger.jsonl` + 卡点分析 | new | SC-1, SC-2 | [ ] |
| `evals/main-pipeline-quality/corpus.yaml` | modified | SC-3 | [ ] |
| `src/akb2/quality/calibration.py` | modified | SC-4 | [ ] |
| `config/main_sources.yaml` | modified | SC-5 | [ ] |
| `src/akb2/quality/intake/extract.py` | modified | SC-5 | [ ] |
| `reports/source-validation/sources-ledger.jsonl` | modified | SC-5 | [ ] |
| `reports/calibration/live-sample.json` | new | SC-1（SC-9） | [ ] |

---

## 版本记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v1 | 2026-06-15 | 初始版本：P0 后半段实测 + P1 校准修复 + P2 源尾巴；承接本会话 G1-G3（intake archived 0→4） |
| v2 | 2026-06-16 | 计划审查 REQUIRES_CHANGES 修复：R1[P1] 加 Step P0.0 `--staging-dir` 隔离参数 + ledger_path 硬断言（防污染真 staging）；R2[P2] 临时目录 `_tmp/cql-p0/`→`/tmp/akb2-cql-p0/`（移出 repo 避 gitignore 问题）；R3[P2] P0.2 加路由/预算 gate（SC-8）；残余风险：SC-9 标注为外部 spec、raw 源文件改持久绝对路径 + 重生成兜底 |
| v3 | 2026-06-16 | 第二轮审查 REQUIRES_CHANGES 修复：F1[P1] SC-1 顶层 verify 命令补 `--staging-dir`（之前漏同步，仍会绕过隔离）；F2[P2] Step P0.0 verify 改为 CLI 解析级（不再要求 ledger 存在，修顺序矛盾——P0.0 早于 P0.1 建 raw）；残余：依赖段 live2 路径同步为持久绝对路径、SC-7 改用真 staging before/after mtime+sha256 证明零写入（不依赖真 vault 是否 git repo） |
| v4 | 2026-06-17 | 实测修订 + C+ 续作定向（见下「实测修订」节）：本计划 P0 已暴露 repair 子循环空转（addy 卡 grade 7→10→12 三轮不收敛）；C+ 重写 repair 为 agent 爬坡是本计划的自然续作 |

---

## 实测修订（2026-06-17 · C+ 续作）

> 权威细节见 `docs/loops-design-decisions.md` §12 与 `specs/content-quality-loop/spec.md` 顶部修订块 + Addy Osmani《Loop Engineering》/ Boris Cherny 炉边谈话调研。

**本计划 P0 的实测产出（= 续作的依据）**
- 后半段（准入→提炼→评分→修复→升格）首次真跑，**骨架贯通，repair 是唯一撞墙节点**：黄金样张 addy 卡 grade 7→修后 10→12，3 轮不收敛（越修越坏）。
- 这证实本计划 §背景判断（SC-3 0% 主因是 defuddle bug + 虚构语料，非管线本身）只对了一半：intake/前半段确实修好了，**但 repair 本身确有结构性缺陷**。

**repair 空转 4 根因**：① grader location 词表 ≠ repair 小节定位 ② 只认 H2 不认 H3 ③「整页重写+逐字节 diff」自相矛盾→整轮丢弃 ④ 超时静默吞（`repair.py:246`）。

**C+ 续作（本计划非目标曾写"不改 repair 判定逻辑，除非 P0 实测暴露 bug，暴露即停下单独决策"——现已触发该条款，用户已授权改 `repair.py`）**
- 重写 repair 节点 → 「缺陷清单 + 黄金样本 → agent 定点爬坡（切节-改节-拼回）→ grader 作 `/goal` 停止条件」；统一 grader↔repair location 契约；认 H3；超时不静默。
- 验收门 = addy 卡端到端收敛（接本计划 P0.4 通过率口径，目标从"0% 收敛"→"≥1 张样张卡走完全链"）。
- `grader.py`/量规零改动（forbidden 不变）；门控/准入/五闸保持确定性 fail-closed（知识库误放成本高，不照搬"模型自己会做对"）。
