# GitHub Curator 日推 MVP v1（5 步精简版）

GD_STANDARD: Project GD/prompts/gd-review-standard.md
GOAL_SOURCE: Project GD/docs/gd-v7-project-goal.md
TEMPLATE_KIND: gd-master-plan

> 创建日期：2026-05-27
> Worktree：`plan/akb2-gd-github-curator`
> 隔壁并行链路：`worktree-akb2-gd-plan-social-trends`（owned_paths 已对齐，详见 §6.1）

## 1. 目标链

- **PROJECT_GOAL**：AKB2.0 自动产出每日"知识库情报简报"，覆盖社交热点 + GitHub 高热项目两条独立平行链路
- **CHAIN_GOAL**（本 plan）：每日产出 1 份 GitHub 高热项目简报，源自 100 人 X 博主候选池的"今日活跃"动态名单，落 vault `$VAULT_ROOT/AKB2.0/AI TRENDS/GitHub/<下一日> GitHub Briefing.{md,json}`（`VAULT_ROOT` env 必填，禁止相对路径）
- **PHASE_GOAL**（MVP v1）：跑通 8 步流水线（intake → URL 抽取 → aggregator → GitHub 富化 → filter → scoring → synthesis → render+gate+vault），单日产出 top10 项目含 200 字摘要

## 2. Review 对齐

- REVIEW_DOMAIN: `pipeline-design`
- REVIEW_FOCUS: 数据链路真实性; 隔离边界（与 social-trends 不交叉）; 三层 fallback 切换逻辑; 速率限制可控性; schema 强约束
- PLAN_SOURCE: `approved_plan`（设计依据 [[project-akb2-github-curator-chain-design]] v3）

## 3. 成功标准（SC）

| SC | 标题 | verify 命令/路径 |
|----|------|----------------|
| SC-0 | schema + validator 双向（正例 PASS / 反例 FAIL）| `test -f src/akb2/briefing/github/schemas/github_briefing_v1.json && PYTHONPATH=src python -m akb2.briefing.github.validator tests/fixtures/briefing/github/schema-valid-sample.json \| grep "OK" && ! PYTHONPATH=src python -m akb2.briefing.github.validator tests/fixtures/briefing/github/schema-invalid-missing-summary.json` |
| SC-1 | URL 归一化与 dedup helper 通过 | `pytest tests/briefing/github/test_url_normalizer.py tests/briefing/github/test_dedup.py -v \| grep "passed"` |
| SC-2 | Layer 1 intake：池跑完 < 12min，失败率 < 50%（降级红线；生产期望 < 10%） | `python -m akb2.briefing.github.intake.twitter_collector --pool config/github_curator_pool.yaml --report json \| jq -e '.fail_rate < 0.5 and .duration_min < 12' > /dev/null` |
| SC-3 | Layer 2 fallback：HTML 抓 Trending 解析 ≥ 10 个 repo + 经 enricher/scoring/synthesis 后产物过 schema validator + top_repos==10 | `python -m akb2.briefing.github.intake.trending_html_collector --report json \| jq -e '.repos \| length >= 10' > /dev/null && export VAULT_ROOT="$(pwd)/tests/fixtures/vault-stub" && bash scripts/daily_github_briefing.sh --mock --force-layer 2 && D=$(date -v+1d +%Y-%m-%d) && BASE="$VAULT_ROOT/AKB2.0/AI TRENDS/GitHub/$D GitHub Briefing" && PYTHONPATH=src python -m akb2.briefing.github.validator "$BASE.json" \| grep OK && jq -e '.fallback_layer_used == "layer2" and (.top_repos \| length == 10)' "$BASE.json" > /dev/null` |
| SC-4 | fallback judge 三态：Layer1 OK / Layer1 失败转 Layer2 / Layer3 mock 注入 | `pytest tests/briefing/github/test_fallback_judge.py -v \| grep "passed"` |
| SC-5 | enricher + scoring 6 维（cold-start 跳过 stars_velocity） | `pytest tests/briefing/github/test_enricher.py tests/briefing/github/test_scoring.py -v \| grep "passed"` |
| SC-6 | synthesis batch 模式：10 repos 一次 LLM 调用，<= 12K input tokens | `python -m akb2.briefing.github.synthesis --fixture tests/fixtures/briefing/github/enriched-sample.json --dry-run \| jq -e '.input_tokens <= 12000' > /dev/null` |
| SC-7 | pipeline 端到端 mock：8 步跑通，产出 `.md` + `.json` 同对儿，JSON 过 schema validator，top_repos 严格 = 10 | `export VAULT_ROOT="$(pwd)/tests/fixtures/vault-stub" && bash scripts/daily_github_briefing.sh --mock && D=$(date -v+1d +%Y-%m-%d) && BASE="$VAULT_ROOT/AKB2.0/AI TRENDS/GitHub/$D GitHub Briefing" && test -f "$BASE.md" && test -f "$BASE.json" && PYTHONPATH=src python -m akb2.briefing.github.validator "$BASE.json" \| grep OK && jq -e '.top_repos \| length == 10' "$BASE.json" > /dev/null` |
| SC-8 | vault 落点 + 文件名 + launchd idempotent bootstrap（本地 5:30 = UTC 前一日 21:30；与社媒 7:30/8:30 错峰）+ wrapper 脚本存在且可执行 + plist 指向 wrapper + plist 无 EnvironmentVariables + 无 token literal | `D=$(date -v+1d +%Y-%m-%d) && test -f "tests/fixtures/vault-stub/AKB2.0/AI TRENDS/GitHub/$D GitHub Briefing.md" && test -x scripts/launchd_wrapper.sh && grep -q launchd_wrapper.sh launchd/com.akb2.github_briefing.plist && ! grep -q '<key>EnvironmentVariables</key>' launchd/com.akb2.github_briefing.plist && ! grep -RE -e 'ghp_[A-Za-z0-9_]{20,}' -e 'sk-[A-Za-z0-9_-]{20,}' -e 'gho_[A-Za-z0-9_]{20,}' launchd/ scripts/ src/ && (launchctl bootout gui/$(id -u)/com.akb2.github_briefing 2>/dev/null \|\| true) && launchctl bootstrap gui/$(id -u) launchd/com.akb2.github_briefing.plist && launchctl print gui/$(id -u)/com.akb2.github_briefing \| grep launchd_wrapper.sh && launchctl print gui/$(id -u)/com.akb2.github_briefing \| grep 'hour = 5'` |
| SC-9 | 真实生产跑 1 次（@PraiseZhu00 抓池 + GitHub API + DeepSeek synthesis + 落 `$VAULT_ROOT` 绝对路径下 `.md` + `.json` 同对儿，JSON 过 schema validator，top_repos 严格 = 10，cwd 内无同名误写） | `: ${VAULT_ROOT:?VAULT_ROOT must be set} && [[ "$VAULT_ROOT" = /* ]] && bash scripts/daily_github_briefing.sh \| grep "briefing: WROTE" && D=$(date -v+1d +%Y-%m-%d) && BASE="$VAULT_ROOT/AKB2.0/AI TRENDS/GitHub/$D GitHub Briefing" && test -f "$BASE.md" && test -f "$BASE.json" && PYTHONPATH=src python -m akb2.briefing.github.validator "$BASE.json" \| grep OK && jq -e '.top_repos \| length == 10' "$BASE.json" > /dev/null && ! test -f "AKB2.0/AI TRENDS/GitHub/$D GitHub Briefing.md"` |

## 4. 非目标（NON_GOALS）

- ❌ `stars_velocity_7d` 维度上线（冷启动期跳过，v1.1 上）
- ❌ 多语言 README 翻译（DeepSeek 原文直摘要）
- ❌ 简报推送外部渠道（Telegram/邮件）— v1 仅落 vault
- ❌ 候选池 web UI 管理 — yaml 直编辑
- ❌ scoring 权重学习/自动调优 — v1 写死，v2 看反馈
- ❌ 自动账号切换（@PraiseZhu00 ↔ @PraiseZhu52）— v1 触发严限流后整日 skip，人工切浏览器
- ❌ Layer 2 兜底输出质量打分 — 简报标 `fallback_layer_used: layer2`，质量由人工 review
- ❌ acceptance 7 天数据观察报告 — 进 v1.1（见 §9）

## 5. Step 拆分（5 步）

| Step | 名称 | owned_paths | blocked_by | can_parallel_with | 主要 SC |
|------|------|------------|-----------|-------------------|---------|
| 0 | 基础设施：schema + validator + URL 归一化 + dedup + 100 人池 yaml | `src/akb2/briefing/github/__init__.py`, `src/akb2/briefing/github/schemas/github_briefing_v1.json`, `src/akb2/briefing/github/validator.py`, `src/akb2/briefing/github/url_normalizer.py`, `src/akb2/briefing/github/dedup_sqlite.py`, `config/github_curator_pool.yaml`, `tests/fixtures/briefing/github/schema-*.json`, `tests/briefing/github/test_validator.py`, `tests/briefing/github/test_url_normalizer.py`, `tests/briefing/github/test_dedup.py` | — | — | SC-0, SC-1 |
| 1 | 三层 intake：twitter-cli + trending HTML + manual JSON + fallback judge | `src/akb2/briefing/github/intake/__init__.py`, `src/akb2/briefing/github/intake/twitter_collector.py`, `src/akb2/briefing/github/intake/trending_html_collector.py`, `src/akb2/briefing/github/intake/manual_json_collector.py`, `src/akb2/briefing/github/intake/fallback_judge.py`, `tests/briefing/github/test_intake.py`, `tests/briefing/github/test_fallback_judge.py` | step-0 | — | SC-2, SC-3, SC-4 |
| 2 | enricher + scoring 6 维 | `src/akb2/briefing/github/enricher.py`, `src/akb2/briefing/github/scoring.py`, `tests/briefing/github/test_enricher.py`, `tests/briefing/github/test_scoring.py` | step-1 | step-3 | SC-5 |
| 3 | synthesis batch + DeepSeek prompt | `src/akb2/briefing/github/synthesis.py`, `docs/github-deepseek-prompt-v1.md`, `tests/briefing/github/test_synthesis.py`, `tests/fixtures/briefing/github/enriched-sample.json` | step-1 | step-2 | SC-6 |
| 4 | pipeline + launchd + 真跑验证 | `src/akb2/briefing/github/pipeline.py`, `scripts/daily_github_briefing.sh`, `scripts/dry_run_github_e2e.sh`, `scripts/launchd_wrapper.sh`, `scripts/bootstrap_launchd.sh`, `launchd/com.akb2.github_briefing.plist`, `config/github-briefing.env.example`, `tests/fixtures/vault-stub/AKB2.0/AI TRENDS/GitHub/**`, `tests/briefing/github/test_pipeline.py` | step-2, step-3 | — | SC-7, SC-8, SC-9 |

### 5.1 Step 详细字段

#### step-0 基础设施

WHERE: `src/akb2/briefing/github/__init__.py`, `src/akb2/briefing/github/schemas/github_briefing_v1.json`, `src/akb2/briefing/github/validator.py`, `src/akb2/briefing/github/url_normalizer.py`, `src/akb2/briefing/github/dedup_sqlite.py`, `config/github_curator_pool.yaml`, `tests/fixtures/briefing/github/schema-*.json`, `tests/briefing/github/test_{validator,url_normalizer,dedup}.py`
WHAT: 写 github_briefing_v1 schema（jsonschema draft-07）+ jsonschema 直调 validator + URL 归一化函数（lowercase + 剥尾巴）+ SQLite 3 天滚动去重表 + 从 `data/curator_check_v2/result2.txt` 转录 60+ OK handle 进 yaml。
WHY: schema 是后续 4 步的契约根；URL 归一化决定 dedup 正确性；pool yaml 是 Layer 1 抓取源；这 5 件事任一缺失下游全部跑不通。
VERIFY: SC-0（schema 正反例双跑）+ SC-1（pytest test_url_normalizer + test_dedup 通过）。
forbidden_paths: `src/akb2/briefing/github/intake/**`, `src/akb2/briefing/github/{enricher,scoring,synthesis,pipeline}.py`, `scripts/**`, `launchd/**`, `docs/**`, social-trends 整片 `src/akb2/briefing/`（不含 github/）
required_context: `~/.claude/projects/-Users-praise-AI-Agent-Claude-projects-Project-AKB2/memory/project_akb2_github_curator_chain_design.md` v3（设计快照）, `data/curator_check_v2/result2.txt`（60+ OK handle 来源）, 本 plan §6.4 §6.5 §6.7（scoring / schema / URL 规则）
deliverables: 5 源文件 + 3 测试 + 1 yaml + 2 schema fixture（正例 + 反例 missing-summary）

#### step-1 三层 intake

WHERE: `src/akb2/briefing/github/intake/__init__.py`, `src/akb2/briefing/github/intake/twitter_collector.py`, `src/akb2/briefing/github/intake/trending_html_collector.py`, `src/akb2/briefing/github/intake/manual_json_collector.py`, `src/akb2/briefing/github/intake/fallback_judge.py`, `tests/briefing/github/test_{intake,fallback_judge}.py`
WHAT: Layer 1 用 twitter-cli 跑动态池 + 入口 health check + sleep=5s + 429 指数退避；Layer 2 用 curl + Chrome UA + BeautifulSoup 解析 trending HTML；Layer 3 读 `data/manual/<YYYY-MM-DD>.json` 过 schema 验证；fallback_judge 集中判定三层切换（candidates<5 OR `failed_handles/len(pool) ≥ 0.5` 触发降级）。
WHY: Layer 1 是主路径但单点风险高（账号 ban / cookie 过期 / X 改协议）；没有 Layer 2/3 兜底则任一故障当日产出空简报。这是 plan 最复杂的代码。
VERIFY: SC-2（Layer 1 池 < 12min 失败率 < 0.5）+ SC-3（Layer 2 ≥ 10 repo）+ SC-4（fallback_judge 三态 pytest）。
forbidden_paths: `src/akb2/briefing/github/{schemas,validator,url_normalizer,dedup_sqlite,enricher,scoring,synthesis,pipeline}.py`, `config/github_curator_pool.yaml`, `scripts/**`, `launchd/**`, social-trends 整片 briefing
required_context: step-0 产出全部（schema / validator / url_normalizer / dedup_sqlite / pool yaml）, 本 plan §6.2 §6.3 §6.10
deliverables: 4 collector + 1 fallback_judge + 2 测试

#### step-2 enricher + scoring

WHERE: `src/akb2/briefing/github/enricher.py`, `src/akb2/briefing/github/scoring.py`, `tests/briefing/github/test_{enricher,scoring}.py`
WHAT: enricher 调 GitHub API（PAT 鉴权）拉 stars / language / topics / README 头 500 字 + 7 天 commits；scoring 用 §6.4 公式算 6 维加权（cold-start 期 stars_velocity = 0）。
WHY: 仅 mention_count 排序会让"小众但博主 RT 多"的 repo 顶到 top10；6 维 + safety 强降权过滤 crypto-shill / NSFW。
VERIFY: SC-5（pytest test_enricher + test_scoring 通过）。
forbidden_paths: `src/akb2/briefing/github/{schemas,validator,url_normalizer,dedup_sqlite,synthesis,pipeline}.py`, `src/akb2/briefing/github/intake/**`, `scripts/**`, `launchd/**`, social-trends 整片 briefing
required_context: step-0 schema / validator / url_normalizer, step-1 candidates 输出格式（CollectorResult），本 plan §6.4
deliverables: enricher.py + scoring.py + 2 测试

#### step-3 synthesis + prompt

WHERE: `src/akb2/briefing/github/synthesis.py`, `docs/github-deepseek-prompt-v1.md`, `tests/briefing/github/test_synthesis.py`, `tests/fixtures/briefing/github/enriched-sample.json`
WHAT: DeepSeek V4 Pro batch 调用（10 repos 一次 prompt）+ XML escape 用户输入 + `<repo>` 标签 fence 防 prompt injection + 输出 JSON 数组 schema 校验；prompt 模板 v1 落 `docs/`。
WHY: synthesis 是简报质量的瓶颈，prompt 设计决定 80% 输出质量；batch 模式比 per-repo 省 token 10x。
VERIFY: SC-6（batch 调用 input tokens ≤ 12K，dry-run jq 校验）。
forbidden_paths: `src/akb2/briefing/github/{schemas,validator,url_normalizer,dedup_sqlite,enricher,scoring,pipeline}.py`, `src/akb2/briefing/github/intake/**`, `scripts/**`, `launchd/**`, social-trends 整片 briefing
required_context: step-0 schema, step-1 candidates 输出格式, step-2 enricher 输出格式（enriched repos with stars/topics/readme_excerpt），本 plan §6.11
deliverables: synthesis.py + prompt v1 doc + 1 测试 + 1 enriched fixture

#### step-4 pipeline + launchd + 真跑

WHERE: `src/akb2/briefing/github/pipeline.py`, `scripts/daily_github_briefing.sh`, `scripts/dry_run_github_e2e.sh`, `scripts/launchd_wrapper.sh`, `scripts/bootstrap_launchd.sh`, `launchd/com.akb2.github_briefing.plist`, `config/github-briefing.env.example`, `tests/fixtures/vault-stub/AKB2.0/AI TRENDS/GitHub/**`, `tests/briefing/github/test_pipeline.py`
WHAT: 串联 8 步流水线（intake → URL 抽取 → aggregator → enricher → filter → scoring → synthesis → render+gate+vault）；落产物同时 `.md` + `.json` sidecar 同对儿；launchd plist 注册本地 5:30（北京，与社媒 7:30/8:30 错峰，github 先跑独占干净 X 账号）触发；plist 禁用 `EnvironmentVariables` 块（连同 VAULT_ROOT 也走 wrapper 注入）；**idempotent bootstrap 合约**：交付一个 `scripts/bootstrap_launchd.sh`，先 `launchctl bootout gui/$(id -u)/com.akb2.github_briefing 2>/dev/null` 容错移除旧注册，再 `launchctl bootstrap gui/$(id -u) launchd/com.akb2.github_briefing.plist`，最后用 `launchctl print gui/$(id -u)/com.akb2.github_briefing` 断言 hour=5 + 指向 wrapper；写完跑一次真生产数据验收。
WHY: 前 4 步分别独立 testable，但 pipeline 才是真链路；launchd 是部署交付物；真跑是端到端 sanity check。
VERIFY: SC-7（pipeline mock 端到端通过 + `.md`+`.json` 同对儿 + top_repos==10）+ SC-8（vault 文件 + launchd 注册 + 本地 5:30 时段）+ SC-9（真跑 1 次落 `.md`+`.json` 同对儿 + top_repos==10）。
PIPELINE_OUTPUT_CONTRACT: 入口先断言 `VAULT_ROOT` 非空且绝对路径（不满足 → 写 `briefing-skipped.json: reason=vault_root_missing_or_relative`）；仅当 `len(top_repos) == 10` 且 schema validator PASS 才输出 `briefing: WROTE` + 同时落 `$VAULT_ROOT/AKB2.0/AI TRENDS/GitHub/<date> GitHub Briefing.md` + 同对儿 `.json`；否则写 `briefing-skipped.json`（含 reason / candidates_count / fallback_layer_used）+ 退出码 0（launchd 不视为失败）。空 top_repos fixture 必须让 validator FAIL（schema minItems=10 已保证）。launchd plist **禁止 EnvironmentVariables 含 secret 字面值**，所有 secret + VAULT_ROOT 必须 wrapper script `source ~/.config/akb2/github-briefing.env`（0600）后再 exec pipeline。
forbidden_paths: `src/akb2/briefing/github/{schemas,validator,url_normalizer,dedup_sqlite,enricher,scoring,synthesis}.py`, `src/akb2/briefing/github/intake/**`, social-trends 整片 briefing, 任何 production `AKB2.0/AI TRENDS/Social/`, `~/.config/akb2/*.env`（不在 repo 内创建/提交）
required_context: 全部 step-0/1/2/3 产出，本 plan §6.2 §6.6 §6.8 §6.9
deliverables: pipeline.py + 4 脚本（daily_github_briefing.sh + dry_run_github_e2e.sh + launchd_wrapper.sh + bootstrap_launchd.sh，全部 +x）+ 1 plist（ProgramArguments 指向 wrapper，无 `EnvironmentVariables` 块）+ `config/github-briefing.env.example`（占位符模板，告诉用户 cp 到 `~/.config/akb2/github-briefing.env` 后填值）+ 1 .gitkeep + 1 测试 + acceptance .md/.json 同对儿（在 vault-stub 下） + bootstrap_launchd.sh 验收脚本必须可在干净机器上 idempotent 重跑（先 bootout 容错再 bootstrap）

## 5a. Wave Matrix

| Wave | Steps | child_agent_count | 并行前提验证 |
|------|-------|-------------------|------|
| 1 | step-0 | 1 | — |
| 2 | step-1 | 1 | blocked_by step-0；step-1 调 step-0 validator + url_normalizer + dedup |
| 3 | step-2 + step-3（并行）| 2 | owned_paths 完全独立；均 blocked_by step-1 输出 candidates schema |
| 4 | step-4 | 1 | blocked_by step-2 + step-3 |

**4 个 wave，max_parallel = 2（wave 3），符合 GD 上限**。

## 6 关键设计契约

### 6.1 owned_paths 边界 vs social-trends

| 资产 | social-trends 拥有 | github-curator 拥有 |
|------|-------------------|-------------------|
| `src/akb2/briefing/__init__.py` | ✅（不动）| — |
| `src/akb2/briefing/schemas/social_trends_v1.json` | ✅ | — |
| `src/akb2/briefing/validator.py` | ✅（不动）| — |
| `src/akb2/briefing/intake/` | ✅ | — |
| `src/akb2/briefing/scoring.py` / `synthesis.py` / `pipeline.py` | ✅ | — |
| `src/akb2/briefing/github/**` | — | ✅ **整子包** |
| `src/akb2/briefing/github/schemas/github_briefing_v1.json` | — | ✅（自有 schemas 子目录避免文件级冲突）|
| `src/akb2/briefing/github/validator.py` | — | ✅（独立 validator，jsonschema 直调，不依赖父级）|
| `config/github_curator_pool.yaml` | — | ✅ |
| `config/intake_sources.yaml` | ✅（不动）| — |
| `scripts/daily_briefing.sh` | ✅ | — |
| `scripts/daily_github_briefing.sh` / `dry_run_github_e2e.sh` / `launchd_wrapper.sh` | — | ✅ |
| `config/github-briefing.env.example`（无 secret 模板）| — | ✅ |
| `launchd/com.akb2.briefing.plist` | ✅ | — |
| `launchd/com.akb2.github_briefing.plist`（只引 wrapper，无 secret 字面值）| — | ✅ |
| `tests/briefing/` | ✅ 根目录 | — |
| `tests/briefing/github/` | — | ✅ |
| `tests/fixtures/briefing/` | ✅ 根目录 | — |
| `tests/fixtures/briefing/github/` | — | ✅ |
| `tests/fixtures/vault-stub/AKB2.0/AI TRENDS/Social/` | ✅ | — |
| `tests/fixtures/vault-stub/AKB2.0/AI TRENDS/GitHub/` | — | ✅ |
| `reports/acceptance/2026-05-27.md`（social-trends 收尾） | ✅ | — |

**核心隔离原则**：所有 GitHub 链路代码住 `src/akb2/briefing/github/` 子包，含自己的 schemas / validator，**不复用父级 validator.py**，**不依赖 social-trends step-0 是否完成**。两条链路只在 vault 写入路径上区分（`Social/` vs `GitHub/`）。

**握手动作**：master-plan 落地后向 social-trends worktree PR 一行注脚到他们 master-plan §6 表，写明 "`src/akb2/briefing/github/` 子包归 `plan/akb2-gd-github-curator` 管"。

### 6.2 三层 fallback 链路

```
Layer 1: twitter-cli user-posts (动态池 → 今日活跃子集)
         ↓ fallback_judge 判定：candidates < 5 OR failure_rate ≥ 0.5
Layer 2: curl github.com/trending + HTML parse (16 articles 已验证)
         ↓ Layer 2 失败 OR parsed_count < 10
Layer 3: 读 data/manual/<YYYY-MM-DD>.json（schema 同 candidates[].json）
         ↓ 文件不存在 OR top_repos < 10（含 enricher + scoring + filter 后）
         当日 skip → 写 briefing-skipped.json，pipeline 不输出 "briefing: WROTE"
```

**统一阈值契约**：`failure_rate = failed_handles / len(pool)`（动态分母，不写死 100）。唯一阈值 `FALLBACK_FAILURE_RATE_THRESHOLD = 0.5`。SC-2 验证 production 节奏下 < 0.5（即 fallback 不该被触发）；生产期望 < 0.1（仅文档说明，不在 SC 强卡）。

**fallback_judge 接口契约**：

```python
# fallback_judge.py
class FallbackDecision(NamedTuple):
    selected_layer: Literal["layer1", "layer2", "layer3", "skip"]
    reason: str
    candidates: list[Candidate]  # 选中层的输出

def decide(
    layer1_result: CollectorResult,
    layer2_runner: Callable[[], CollectorResult],
    layer3_path: Path,
) -> FallbackDecision: ...
```

- Layer1 失败判定：`failure_rate = failed_handles / len(pool) ≥ FALLBACK_FAILURE_RATE_THRESHOLD` OR `len(candidates) < 5`
- Layer2 失败判定：`parsed_count < 10` OR HTTP 非 200
- Layer3 检查：文件存在且 schema 合法
- **top_repos 不足 10 兜底**：三层全跑完后若 enricher+scoring+filter 后 `len(top_repos) < 10`，写 `briefing-skipped.json`（含原因 + 当日 candidates 数 + fallback_layer_used），不写 `.md` 也不输出 `briefing: WROTE`
- **Layer 2/3 来源 normalization 契约**（避免 schema 字段缺失）：
  - Layer 2 Trending HTML 输出转 `Candidate` 时强制填占位：`mentioner="@github_trending"`, `tweet_id=None`, `mentioned_at=<collection_time ISO>`, `tweet_text=None`。 aggregator 对 `mentioner="@github_trending"` 的 candidates 单一聚合：`mention_count=1`, `mentioners=["@github_trending"]`, `first_mention_at=<collection_time>`
  - Layer 3 manual JSON 用户自填 candidates；schema 要求每条含 `repo_url` + `mentioner`（任意字符串）+ `mentioned_at`；缺字段 abort 走"当日 skip"分支
  - 经 Layer 2/3 产出的 briefing 同样过 schema validator + top_repos==10 强约束（无任何宽松路径）

### 6.3 速率限制常量（来自 prod_pace 验证）

```python
# src/akb2/briefing/github/intake/twitter_collector.py
BASE_SLEEP_BETWEEN_HANDLES = 5  # 秒（@PraiseZhu00 实测 96% 成功率）
BACKOFF_429 = [60, 120, 300]    # 三次退避
MAX_RETRIES_PER_HANDLE = 3
FALLBACK_FAILURE_RATE_THRESHOLD = 0.5  # failed_handles / len(pool) ≥ 此值 → 触发 Layer 2
DAILY_RUN_BUDGET_MIN = 12       # 含失败重试，总时长上限
HEALTH_CHECK_CMD = ["twitter", "status"]  # 入口先验 cookie 有效
```

### 6.4 scoring 权重（v3：信任 + 推文热度，2026-06-04 真实链路验收后改）

**机制变更背景**：原设计以"多人共识热度"为主轴（mention_count 0.30~0.35）。真实链路
全量验收（61 博主单日跑）发现 curator pool 内**多人撞车极罕见**——全天仅 1 个 repo 被
≥2 个博主提到。mention 频次 ≈ 常数 1 → 该维变死权重，top10 实质随机；门槛=2 时 filter
后候选 <10 → 每天 skip 无简报。用户决策（方案 A）：mention 共识退出主轴，改为
**mentioner_authority（谁推荐的）为主信号 + engagement（推文热度）为次要信号**。

| 维度 | 权重 | 计算公式 |
|------|------|---------|
| mentioner_authority | 0.35 | tier 加权 `sum(tier_weight)/3`，tier 1/2/3 = 1.0/0.6/0.3（主信号：谁推荐的）|
| topic_relevance | 0.20 | `len(matched_topics) / 6` |
| engagement | 0.15 | `min(1.0, log10(raw+1)/4.0)`，`raw = likes + 2*retweets + 3*bookmarks`（views 不算）|
| activity | 0.15 | `velocity_7d/50`（启用后）→ fallback `commits_7d/30`；None=0.5 中性，0=死库 |
| stars | 0.10 | `log10(max(stars,1)) / 5` |
| readme_quality | 0.05 | `0.5 * (chars > 1000) + 0.5 * has_code_block` |
| safety | 强降权 | crypto/NSFW/赌博等关键词命中 → score = 0 |

6 个加权维之和 = 1.0。

**独立共识 bonus**：`mention_count >= 2`（2+ 博主独立提到同一 repo = 所有信号里最强、
罕见但高价值）→ 最终分 **+0.08**（不进加权和，封顶 1.0）。共识从"门槛/主轴"降级为"加成"。

**engagement 聚合**：多博主提同一 repo 时取 **MAX**（最火一次背书），不取 sum——sum 会
随被提次数累加，重新引入 mention_count 偏置，与去共识主轴的目标相悖。Layer2/3（无真实
推文）engagement = 0，其余维度承载。

**mention 门槛**（filter 阶段）：固定 **1**（任何 1 个博主提及即够格进候选）。历史：
`max(2, ceil(active/15))` → 固定 2 → 真实数据下候选 <10 全 skip → 降到 1。排序交给 scoring。

### 6.5 简报内容 schema（github_briefing_v1）

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["schema_version", "briefing_type", "generated_at", "date",
               "candidate_pool_size", "active_curators_today", "fallback_layer_used", "top_repos"],
  "additionalProperties": false,
  "properties": {
    "schema_version": {"const": "v1"},
    "briefing_type": {"const": "github-curator"},
    "generated_at": {"type": "string", "format": "date-time"},
    "date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
    "candidate_pool_size": {"type": "integer", "minimum": 0},
    "active_curators_today": {"type": "array", "items": {"type": "string", "pattern": "^@\\w+$"}},
    "fallback_layer_used": {"enum": ["layer1", "layer2", "layer3"]},
    "top_repos": {
      "type": "array",
      "minItems": 10,
      "maxItems": 10,
      "items": {
        "type": "object",
        "required": ["repo_url", "stars", "language", "topic_tags", "mention_count",
                     "mentioners", "first_mention_at", "score", "summary", "use_case"],
        "additionalProperties": false,
        "properties": {
          "repo_url": {"type": "string", "pattern": "^https://github\\.com/[\\w.-]+/[\\w.-]+$"},
          "stars": {"type": "integer", "minimum": 0},
          "language": {"type": ["string", "null"]},
          "topic_tags": {"type": "array", "items": {"type": "string"}},
          "mention_count": {"type": "integer", "minimum": 1},
          "mentioners": {"type": "array", "minItems": 1, "items": {"type": "string", "pattern": "^@\\w+$"}},
          "first_mention_at": {"type": "string", "format": "date-time"},
          "score": {"type": "number", "minimum": 0, "maximum": 1},
          "summary": {"type": "string", "minLength": 50, "maxLength": 500, "description": "项目做什么 — 200 字以内中文，客观陈述功能/技术栈/解决的问题"},
          "use_case": {"type": "string", "minLength": 30, "maxLength": 300, "description": "什么情况下该装 + 什么情况下别装 — 中文，含 1-2 条正向场景 + 1 条反向场景"}
        }
      }
    }
  }
}
```

### 6.6 抓取账号策略

| 角色 | handle | 备注 |
|------|--------|------|
| 主 | @PraiseZhu00 | CLI 当前认证；2026-05-27 注册新号；早期 sleep ≥ 5s 养号 |
| 备 | @PraiseZhu52 | 2022 老号 / 97 following / 浏览器已登 |
| ❌ | @PraiseZhu | suspended，仅可读不可写 |

**切换协议**：主号触发严限流（连续 3 天 fail_rate > 30%）→ 人工浏览器切到 @PraiseZhu52，CLI 自动跟上。v1 不做自动切换。

**链路启动 health check**：collector 入口先跑 `twitter status`，未 authenticated 立即 abort 并写 `briefing-skipped.json`，避免输出空简报。

### 6.7 URL 归一化规则

```python
# url_normalizer.py
def normalize(url: str) -> str | None:
    """github.com/X/Y/anything → https://github.com/x/y（lowercase, 剥尾巴）"""
    # 1. 提取 host + owner + repo（容忍 http/https, www, 大小写）
    # 2. 剥离 /issues, /pull, /blob, /tree, /commit, /releases 等子路径
    # 3. 剥离 .git 后缀、? 查询串、# 片段
    # 4. lowercase owner + repo
    # 5. 验证 owner/repo 各 ≤ 39 字符（GitHub 限制）
    # 6. 不符合返回 None
```

测试覆盖：6+ URL 形式（带 issues / blob / tree / .git / 大小写混合 / 带 #fragment）+ 3+ 非法形式（仅 owner、空 repo、非 github 域）。

### 6.8 部署拓扑

| 节点 | 职责 | 验证依据 |
|------|------|---------|
| 本机 Mac | 开发 / 调试 / 验证 / 手动跑 `daily_github_briefing.sh`（必须 export `VAULT_ROOT` 绝对路径） | — |
| Mac mini | launchd 调度（每日本地 5:30 北京；launchd `StartCalendarInterval.Hour=5 Minute=30`；与社媒 7:30/8:30 错峰，github 先跑独占干净 X 账号 ~1h，留 60min 限速恢复缓冲）+ Chrome 浏览器同步 @PraiseZhu00 / @PraiseZhu52 cookie + twitter-cli 自动认证 + `VAULT_ROOT` 由 wrapper 注入 | 用户确认浏览器数据已同步；mac mini 时区必须为北京 |

**Mac mini 防睡眠**：部署时设 `pmset -a sleep 0`（或至少 displaysleep 0 + disksleep 0）。

**VAULT_ROOT 契约**：
- v1 必填 env，必须绝对路径，pipeline 启动断言不为空且 `[[ "$VAULT_ROOT" = /* ]]`
- 不满足直接 abort + 写 `briefing-skipped.json: reason=vault_root_missing_or_relative`
- 禁止任何脚本/代码用相对路径 `AKB2.0/AI TRENDS/...`（除测试 fixture `tests/fixtures/vault-stub/`）
- **mock 模式调用约定**：`daily_github_briefing.sh --mock` 不再自动改 VAULT_ROOT；调用方（含 SC-3/SC-7）必须显式 `export VAULT_ROOT="$(pwd)/tests/fixtures/vault-stub"` 后再调用，保证 mock 走与 prod 完全一致的路径断言分支

### 6.9 密钥存储（plist 严禁存 secret 字面值）

| 密钥 | 本机 dev | Mac mini prod |
|------|---------|---------------|
| DeepSeek V4 Pro API key | `~/.config/akb2/github-briefing.env`（0600，gitignored）| 同左路径，由 wrapper script 加载 |
| GitHub PAT（仅 read public repo metadata） | 同上 .env | 同上 |
| X cookie | 浏览器自动管理 | 浏览器同步自动管理 |
| VAULT_ROOT（非 secret 但必填路径）| `~/.config/akb2/github-briefing.env` 或 shell export | 由 wrapper script 注入 launchd |

**launchd plist 强约束**：
- repo 内 `launchd/com.akb2.github_briefing.plist` 仅引用 wrapper 脚本（`scripts/launchd_wrapper.sh`），**禁止 plist 整体出现任何 `EnvironmentVariables` 块**（连同非密钥的 env 注入也走 wrapper）
- wrapper 启动时 `source ~/.config/akb2/github-briefing.env`（0600 + gitignored），失败直接 abort
- 验收两条独立检查（**只查真实 token literal，env var 名引用允许**）：
  1. token literal 扫描：`grep -RE -e 'ghp_[A-Za-z0-9_]{20,}' -e 'sk-[A-Za-z0-9_-]{20,}' -e 'gho_[A-Za-z0-9_]{20,}' launchd/ scripts/ src/` 必须 0 命中（注意：markdown 表格内禁用 `|` alternation，必须用多 `-e` 形式，否则 `\|` 会被 grep 当字面字符）
  2. plist EnvironmentVariables 块禁令：`! grep -q '<key>EnvironmentVariables</key>' launchd/com.akb2.github_briefing.plist`

**禁止 commit**：`.env`、`*.key`、`*.token.json`、`~/.config/akb2/` 已在全局 .gitignore + secret-scan hook 把关。

### 6.10 三层 intake 接口契约（最复杂步骤的设计预定）

```python
# intake/base.py（不存在；契约写进 step-1 各 collector 文件）
class Candidate(NamedTuple):
    repo_url: str           # 已归一化
    mentioner: str          # @handle
    tweet_id: str | None    # Layer 2/3 可能为 None
    mentioned_at: str       # ISO datetime
    tweet_text: str | None  # Layer 2/3 可能为 None

class CollectorResult(NamedTuple):
    layer: Literal["layer1", "layer2", "layer3"]
    candidates: list[Candidate]
    failed_handles: list[str]  # Layer 1 专属
    duration_sec: float
    ok: bool
    error: str | None

# Layer 1
class TwitterCollector:
    def collect(self, pool_yaml_path: Path) -> CollectorResult: ...
    # 内部：读 pool → 逐人 twitter user-posts → 过滤 isRetweet=False → 提 github URL
    #       → 归一化 → 去重（SQLite 3 天滚动）→ 输出 Candidates

# Layer 2
class TrendingHTMLCollector:
    def collect(self) -> CollectorResult: ...
    # 内部：curl github.com/trending → BeautifulSoup → 解析 article → 归一化
    # 输出 Candidate 必须强制填占位：
    #   mentioner = "@github_trending"
    #   tweet_id = None
    #   mentioned_at = <utc now ISO>
    #   tweet_text = None
    # → aggregator 同源去重后 mention_count=1, mentioners=["@github_trending"], first_mention_at=<utc now>

# Layer 3
class ManualJSONCollector:
    def collect(self, manual_json_path: Path) -> CollectorResult: ...
    # 内部：读 data/manual/YYYY-MM-DD.json → 验证 schema（必填 repo_url+mentioner+mentioned_at）→ 输出
    # 字段缺失则 ok=False + abort 走"当日 skip"
```

### 6.11 synthesis prompt 模板骨架（v1）

落点：`docs/github-deepseek-prompt-v1.md`

```text
<system>
你是 GitHub 项目情报编辑。任务：为以下 N 个 GitHub 仓库各产出两段中文文本：
  ① summary（200 字以内）— 客观陈述项目做什么、技术栈、解决的核心问题
  ② use_case（30-300 字）— 什么情况下该装，什么情况下别装；必须含 1-2 条正向场景 + 1 条反向场景

规则：
- 客观、紧凑、不堆形容词、不重复 README 原话
- summary 回答"是什么"，use_case 回答"什么时候用，什么时候别用"
- 输出 JSON 数组，每项 {"repo_url": ..., "summary": ..., "use_case": ...}
</system>

<input>
<repo url="https://github.com/owner/repo1">
  stars: 1234
  language: Python
  topics: [llm, agent]
  mentioned_by: @simonw, @karpathy
  readme_excerpt: |
    {{README 头 500 字，已 XML escape}}
</repo>
... × 10
</input>

<output_format>
[
  {"repo_url": "...", "summary": "...", "use_case": "..."},
  ...
]
</output_format>

<use_case_example>
正例：你需要在 Python 项目里跑量化回测，希望从数据拉取到信号评估一条链路打通；或公司内部做 alpha 因子研究需要可复现的 baseline。
反例：纯加密货币高频交易场景不适用（项目侧重传统金融时序）。
</use_case_example>
```

**prompt injection 防护**：README 与 tweet text 在喂入前 XML-escape，且用 `<repo>` 标签包裹，避免博主推文/README 中的"ignore previous instructions"逃逸。

### 6.12 简报渲染模板（render 阶段产出 .md）

落点：`src/akb2/briefing/github/pipeline.py` 内 `render_briefing(briefing_json) -> str` 函数。

**模板规范**（vault `.md` 文件结构）：

````text
---
date: <YYYY-MM-DD>
type: github-briefing
source: github-curator
fallback_layer: layer1|layer2|layer3
candidate_pool_size: <int>
active_curators_today: <int>
generated_at: <ISO>
tags:
  - github
  - ai-trends
  - daily
---

# GitHub 项目日报 · <YYYY-MM-DD>

> 今日活跃博主 <N>/<pool_size>，走 <Layer X>。<可选：fallback_layer != layer1 时显式提示"今日走兜底链路，质量需人工 review"。>

## 1. [owner/repo](https://github.com/owner/repo) ⭐ <stars> · <language>

`topic1` `topic2` `topic3` · **<N> mentions**（@h1 · @h2 · @h3）
score <0.xx> · 推荐于 <first_mention_at 短格式>

**做什么**: <summary 200 字>

**适用场景**: <use_case 30-300 字，含正向 + 反向>

---

## 2. [owner/repo-2](...) ...
````

**渲染规则**：
- 1-10 编号 + score 降序
- 博主用纯文本 `@handle`（不用 Obsidian wikilink，避免 vault 里到处生成空白笔记节点）
- repo 链接用 markdown `[owner/repo](URL)`（不用 wikilink，避免污染 vault 结构）
- topic_tags 用 inline code 显示；超过 5 个截断 + `…`
- fallback_layer ≠ layer1 时 TL;DR 第一句必须显式说明"今日走兜底链路"
- frontmatter `tags` 加入 `tags: [github, ai-trends, daily]` 便于 Obsidian dataview 查询

**JSON sidecar 同时落**：`<同名>.json`，内容即 schema v1 完整结构（含 use_case），给程序/SC 验收读，不给人读。

**示例（完整一条）**：

````markdown
## 1. [microsoft/qlib](https://github.com/microsoft/qlib) ⭐ 15k · Python

`quant` `ml` `finance` · **3 mentions**（@simonw · @karpathy · @GitTrend0x）
score 0.87 · 推荐于 2026-05-28T13:22

**做什么**: Qlib 是微软开源的量化金融研究平台，覆盖从原始数据采集、特征工程、模型训练到回测评估的完整链路。内置 ML baseline、benchmark dataset 和 alpha 因子库，支持自定义模型插件。

**适用场景**: 你需要在 Python 项目里搭建量化研究 pipeline，希望从数据到信号评估一条链路打通；或公司内做 alpha 因子研究需要可复现 baseline。不适用于纯加密货币高频交易（项目侧重传统金融时序）。
````

### 6.12 关键设计决策汇总

| 决策 | 落点 | 验证依据 |
|------|------|---------|
| X 抓取走本地 twitter-cli | `intake/twitter_collector.py` | AKB 工具库无替代品（akb_search 2 次 0 结果）|
| 候选池 100 人动态名单 | `config/github_curator_pool.yaml` | 100 候选验证 60+ OK，活跃子集 ≥ 5 触发 Layer 1 |
| 每日 top10 | `synthesis.py` | 用户锁定 |
| 主号 @PraiseZhu00 / 备 @PraiseZhu52 | health check at collector entry | @PraiseZhu suspended 已证实；@PraiseZhu52 2022 老号验证可读 |
| Layer 2 GitHub Trending HTML 可行 | `intake/trending_html_collector.py` | curl + Chrome UA 拿到 16 articles |
| synthesis 走 DeepSeek V4 Pro batch | `synthesis.py` + `docs/github-deepseek-prompt-v1.md` | token 成本 < $0.01/天 |
| 落 vault `AKB2.0/AI TRENDS/GitHub/<下一日> GitHub Briefing.{md,json}` 同对儿 | `pipeline.py` | 与 social-trends 的 Social/ 子目录平行；JSON 给 SC / validator，MD 给人读 |
| launchd 本地 5:30（mac mini 北京时区）| `launchd/com.akb2.github_briefing.plist` 设 `StartCalendarInterval.Hour=5 Minute=30` | launchd 按本地时区解释 Hour 字段；github 排社媒（7:30/8:30）之前先跑，独占干净 X 账号 ~1h 后留 60min 限速恢复缓冲，避免两链路抢同一账号限速额度 |
| URL 归一化强制小写 + 剥尾巴 | `url_normalizer.py` | 避免 dedup 把同 repo 算成多个 |
| mention 门槛固定 1 + 共识降级为 +0.08 bonus（v3） | `scoring.py` `mention_threshold`/`score` | 真实链路验收：撞车极罕见，门槛=2 致每日 skip；改 authority+engagement 主导（见 §6.4）|

## 7 风险表

| ID | 风险 | 等级 | 应对 |
|----|------|------|------|
| R-1 | @PraiseZhu00 新号 72h 内 newcomer 限流升级 | P1 | 第 1-7 天 verbose 日志监控；触发 → 人工切 @PraiseZhu52 |
| R-2 | browser cookie 过期 → CLI 静默 401 | P1 | collector 入口 health check（SC-2 verify 含）|
| R-3 | 100 人池全死 → Layer 2 也抓不到 | P2 | Layer 3 manual JSON 注入；当日 skip 写通知 |
| R-4 | DeepSeek prompt injection（博主推文恶意构造）| P2 | XML escape + `<repo>` 标签 fence（§6.11） |
| R-5 | GitHub Trending HTML 改版 selector 失效 | P2 | 月度回归 + selector 失败时降级 Layer 3 |
| R-6 | mac mini 合盖睡眠 → launchd 跳过 | P1 | 部署设 `pmset -a sleep 0`；launchd `RunAtLoad=true` 补偿 |
| R-7 | 同 repo 多种 URL 写法 dedup 失效 | P3 | `url_normalizer.py` 强制 lowercase + 剥尾巴 |
| R-8 | mention 频次在此 pool 内 ≈ 常数 1（多人撞车极罕见，真实验收证实）| P2 | v3：mention 退出主轴，authority+engagement 主导排序；门槛降 1，共识改 +0.08 bonus（§6.4）|
| R-9 | 与 social-trends merge 冲突 | P2 | 子包隔离 + §6.1 owned_paths 表 + 跟那边 PR 注脚 |
| R-10 | DeepSeek API 故障 → synthesis 失败 | P2 | retry 3 次 + 失败写 `briefing-failed.json` 不写空文件 |
| R-11 | GitHub PAT 速率限制（5000/h 几乎不可能触发）| P3 | enricher 触达 4500/h 时 abort + 通知 |

## 8 Anti-fill 自检

| 检查 | 通过? |
|------|------|
| 每条 SC 含 verify command/path/test | ✓ 全部含 bash/jq/pytest/launchctl 命令 + expect 断言 |
| owned_paths 不交叉（与 social-trends 双向）| ✓ §6.1 表逐项标 |
| Wave concurrency ≤ 2 | ✓ 最高 wave 3 = 2（step-2 + step-3）|
| 无套话 token | ✓ 无 "完善 / 优化 / 系统性 / 全面" |
| 含 PROJECT_GOAL / CHAIN_GOAL / PHASE_GOAL | ✓ |
| 含 REVIEW_DOMAIN / REVIEW_FOCUS / PLAN_SOURCE | ✓ |
| Wave matrix 含 child_agent_count + 并行前提 | ✓ |
| 每条 NON_GOAL 可执行检验"未涉及" | ✓（手动 grep / 文件检查）|
| 风险表含等级 + 应对落点 | ✓ |
| 密钥/账号策略含本机 vs mac mini 切分 | ✓ §6.6 §6.8 §6.9 |
| 最复杂步骤含接口契约（避免实现期歧义）| ✓ §6.10 三层 intake 契约 + §6.11 prompt 骨架 |

## 9 Pending future plans（v1 之后）

| 阶段 | 内容 | 触发条件 |
|------|------|---------|
| v1.0+ | 真跑后 7 天数据观察 + 调参报告 | 真跑后 7 天 |
| v1.1 | 启用 `stars_velocity_7d` 维度 + scoring 权重重分 | v1 跑满 3 天积累数据 |
| v1.2 | 候选池补 6 个 CN typo handle 正确写法（@BuckleyChen 等待验证）| 人工 X 上确认 |
| v1.3 | 自动账号切换（@PraiseZhu00 ↔ @PraiseZhu52）| R-1 触发频次 ≥ 1/week |
| v2 | 简报内容质量评估（人工反馈 + scoring 自动调参）| v1 跑满 30 天 |
| v2 | Telegram / 邮件推送 channel | 用户提出需求 |

---

## 附录 A：100 候选池来源

完整 fetch 脚本与验证产物已持久化到 `data/curator_check_v2/`（`fetch100.sh` / `analyze100.py` / `result2.txt` / `retry.sh`）。
速率验证产物在 `data/rate_limit_test/`（`prod_pace.sh` / `prod_pace.log`）。

step-0 写 `config/github_curator_pool.yaml` 时从 `data/curator_check_v2/result2.txt` 中"OK 状态 + age ≤ 14d + topic 命中 ≥ 1"的 60+ 个 handle 转录。

## 附录 B：相关 memory

- 设计快照：[[project-akb2-github-curator-chain-design]] v3
- 后端：[[project-akb2-llm-backend-switch]]
- 部署：[[project-akb2-deployment-split]]
