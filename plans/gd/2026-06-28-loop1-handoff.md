# AKB2 循环1 改动交接档(2026-06-28,冷启动可续接)

> 配套工件:记忆 `~/.claude/projects/-Users-praise-AI-Agent-Claude-projects-Project-AKB2/memory/project_akb2_loop1_bottleneck_priority_2026-06-28.md`;执行计划 `plans/gd/2026-06-28-loop1-execution-plan.md`;v2 设计 `specs/multi-source-distill/spec.md`。本档是操作层交接(状态/命令/决策/待办),三者互补。

## 0. 一句话现状
AKB2 循环1 满仓校准 18 正样本 **0/18**(负样本 2/2 正确拒)。本会话修了 4 个上游 bug(已提交、离线 1633 绿),但失败**从上游移到了评分+修复段**——核心是一个实锤的确定性 bug:**concept 加权分恒=0**(grader 不吐逐维 score)。下一步是 P0(修加权分等)再重跑。

## 1. 环境与状态(必读)
- **worktree**:`/Users/praise/AI-Agent/Claude/projects/akb2-integ-wt`,分支 `integration/loop1-content-quality`,**HEAD=5dde324**,本会话独占(并行会话已停;不要 git reset,保留并行会话 5 commit:136d999/9d1c822/16406e4/694d423/1ade9d0)。
- **本会话 4 commit**(1ade9d0..HEAD):
  - `76007d4` R1 raw_verify:SG-command-recheck 内联反引号排除散文/repo-slug(救满仓 #0/2/8/10/17)
  - `6f7f04a` R2 grader:concept prompt 修复(解锁误报"grader不可用"的 #1/3/4/5/9/11/14)
  - `3c0c107` B1 repair:透传 known_concepts/pending_queue 到 repair 轮 self-gate(wB blocker)
  - `5dde324` wA#3 runner:concept PASS artifact 落盘异族硬判证据(护栏⑥)
- **离线测试**(必须 pyenv 3.13.12,否则 3 个 video CLI 测试因 Xcode py3.9 挂):
  `PATH=/Users/praise/.pyenv/versions/3.13.12/bin:$PATH PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -m 'not integration' -q` → 1633 passed。
- **满仓校准命令**(grader 走网关 gpt-5.4;key 用环境变量,不要硬编码):
  设 4 个 env 后跑 `python3 -m akb2.quality.calibration --corpus evals/main-pipeline-quality/corpus.yaml --report-out reports/calibration/factory.json`:
  - `AI_GATEWAY_BASE_URL` = `https://llm-proxy.tapsvc.com`
  - `AI_GATEWAY_API_KEY` = 取 ANTHROPIC_API_KEY 环境变量的值(网关凭证)
  - `OPENAI_MODEL_ID` = `codex/gpt-5.4`
  - `PYTHONPATH` = `src`
  - 串行跑 ~50min;报告写 `reports/calibration/factory.json`。
- **ledger**(逐篇真实缺陷在这,worktree 内无):`~/Obsidian/Praise Space/AKB2.0/staging/quality-ledger.jsonl`,abort 行带 `details.final_defects` + `repair_trace_paths`。
- **安全**:① 官方 OPENAI_API_KEY(shell 里已设)会打 tapsvc 网关 401 → 满仓靠"网关 key 优先于官方 key"生效(记忆 7744);别打印任何 key 值。② 历史里疑似泄露过一个 sk- 开头的 key,建议轮换。③ block-no-verify hook 禁 `git --no-verify`;commit 结尾固定 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。

## 2. 满仓 0/18 失败分布(最新一轮,R1+R2 后)
per_segment={repair_loop:13, integrity_floor:2, admission:2, distill_compose:1};rounds={3:12,2:1,0:7};负样本 2/2 拒。
- repair_loop 13:12 篇 `distill_no_converge`(3轮残留缺陷),1 篇(#4 黄金样张)`weighted_score_below_threshold`(加权分 0.0<6.5)。
- integrity_floor 2:#6/#15 procedural F4 无 H1 命令步(lane 误判:概念文判成 procedural)。
- admission 2:#12 crewai / #13 sub-agents non_knowledge/how(gemini-flash 误杀官方文档)。
- distill_compose 1:#16 SG-command-recheck 残留(`和`/表格碎片当命令,R1 没全堵)。

## 3. 核心诊断(7 worker 盘查后)
**问题分三层:**
- **代码 bug**:①加权分=0(grader 不吐 score,gap3 本地复现实锤;weighted_score.py:105-118 + grader.py:360/399)②must-cover 字面量渲染不进操作步骤(#0/2/8/17)③#16 命令残留 ④lane 误判(#6/15)。
- **机制问题(12 篇主因)**:repair 只能"改已存在且 location 命中的 H2/H3 小节 + 5 个确定性回填",**补不出 raw 没有的真机制**;A/B 拉扯;location 命名对不上小节→patch 被丢弃→3 轮空转(gap2)。
- **参数/数据未决**:12 篇 B 层语义缺陷"该救(机制可修)/该调(grader太苛)/该拒(源真薄)"——**gap1 说光看 ledger 判不了,需独立语义审计**(P1 才能定)。

**关键正面信号**:黄金样张 #4 缺陷其实修收敛了(2轮),只死在加权分=0;**修 P0 后它极可能就过 → 验证 concept 通路能跑通**。
**慢诊断**:满仓 ~50min 不是卡死,是 13 篇真跑 3 轮 repair+gpt-5.4 regrade(串行),无 retry storm。

## 4. 核心决策(全部已与用户锁定)
1. **阈值 6.5→6.0**(rubric.yaml concept threshold)。
2. **score 尺度钉死 0~1**(P0 必须含;否则模型吐 0~10 会让阈值 6.5 形同虚设=误放)。
3. **满仓走 active e2e**(`AKB2_AUTO_PROMOTE=1`,不只 proposal)。
4. **准入用 deepseek,砍 gemini-flash**(准入判源不判产物,不破异族;回到 maker=deepseek/checker=gpt-5.4 两模型)。注:gemini-flash 做准入是 2026-06-13 起旧代码漂移,非本会话加。
5. **多源提炼=概念图谱锚定路由**(不用 embedding 聚类——相似≠同一概念会混):提不出页的 raw → deepseek 判挂哪个概念(复用 known_concepts+pending_queue+图谱边);embedding 仅降级做新概念去重召回。不混靠"输入只喂挂该概念的料+F4 因果边连通兜底"双保险。
6. **多源提炼 v1 只做 create**(pending 槽攒够出新页);**update**(新 raw 并入已有页+merge+重评分+版本)留 v2;**升星**(wiki→wiki 无新料的二度/三度深化推高 How)独立未来,与多源提炼正交。
7. **raw 门与 wiki 标准对齐**:同一 taxonomy+同一"什么算机制/知识"定义,差别是"完成度"不是"严格度"——raw 收"对某概念≥1 合格知识贡献"、拒"对任何概念零贡献"(与 wiki 负样本同口径);不另立松阈值。
8. **GD plan 路径 = B(已授权,待执行)**:用户选 B = 走完整 `/gd plan` 机器,但**把 GOAL_SOURCE 从 GD 的 v7 反填表目标换成 AKB2 自己的目标文档**(偏离 gd.md 硬编码的 GOAL_SOURCE,用户已授权此偏离)。原因:gd.md 硬编码 GOAL_SOURCE=Project GD 反填表目标,锚不上 AKB2 工作。

## 5. 执行计划(详见 plans/gd/2026-06-28-loop1-execution-plan.md)
- **P0(无悬念,先做)**:P0-1 加权分必填score+尺度0~1+阈值6 / P0-2 must-cover渲染 / P0-3 #16命令形态 / P0-4 砍gemini准入归deepseek / P0-5 补观测(repair trace+admission lane_reason) / P0-6 active e2e+满仓重跑。出口:离线绿+满仓重跑完成(带trace)。
- **P1(唯一需用户再拍一次)**:读重跑结果 + 派 worker 对 12 篇做语义审计(raw vs候选 vs grader指控)→ 逐篇定"救/调/拒"。
- **P2(据 P1 定性分类处理)**:机制可修→改repair(location容差/A-B拉扯);太苛→调rubric参数;源真薄→corpus标reject/换源;顺带 lane误判/reject后门/测试债。出口:满仓"够格全过+该拒全拒"→Codex异族审→合main。
- **v2(P0后)**:多源提炼(spec 已落)。

## 6. 下一步(给接手者)
1. **执行 GD plan 路径 B**(用户已授权):跑 `/gd plan`,GOAL_SOURCE 用 AKB2 目标(宪法 `docs/constitution.md` + content-lane spec),把 P0/P1/P2/v2 落成 GD master/step/task-packet 套件 + 走 mandatory subagent dispatch(capability probe + dispatch_map + ≤2 child planner + stage-dispatch-ledger + controller-report,全 fail-closed,见 gd.md)。**注意 GD anti-fill 红线**:不生成 generic 改进 token;目标链锚 AKB2 目标。
2. 或直接进 **P0 执行**(若用户要先动代码而非先出 GD 正式 plan)。
3. P0 做完拿黄金样张快速验(几十秒,不跑满仓):concept 候选过 grader + 加权分≥6.0 → 证明通路通。

## 7. 未决/盲区(诚实)
- **12 篇该救/调/拒**:必须 P0 重跑(带 trace)+ 语义审计后才能逐篇定,现在拍是瞎猜。
- **90% 可能不可达且不是 bug**:若审计发现部分源真薄,分层标准正确拒了它们,问题在 corpus 把薄文档标 expected=ingest;"达标"应改为"够格全过+该拒全拒",不硬凑 18。
- v2 的 K 值(攒几篇触发)/超时/harness 改造(1:1→N:1):v2 实现时定。

## 8. 护栏(全程不破)
concept F4 不放宽 / novelty 不豁免 / 负样本必拒 / fail-closed / operations.scan 不收窄 / Maker-Checker 异族分离(grader 保留 interpretation_entailment 硬判) / 二值地板不可用加权补偿 / 不靠造假绿测试。判定级改动改完过 Codex 异族审再合 main。

## 9. 本会话用过的调查 worker(可追溯)
wA-exec-correctness + wB-test-honesty(审 5 批次代码,都 REQUIRES_CHANGES,blocker 已修 B1/wA#3);diag-calib-slow(慢诊断);pipe1/2/3-*(管线分段盘查);gap1-defect-ledger + gap2-repair-capability + gap3-weighted-downstream(盲区盘查)。结论已折进本档第 2-3 节。
