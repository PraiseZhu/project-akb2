# Step 1: save-query-result skill + 触发词

日期：2026-05-17 | blocked_by：— | can_parallel_with：step 2 | SC：SC-1

## 目标

在 knowledge-ops skill 新增 save-query-result 子命令 + 注册触发词，让用户说"存一下"等触发词后，skill 自动从对话提取 query/derived_from/confidence，写候选页到 `staging/candidate/<slug>.md` 并自动跑 lint+gate。

## owned_paths

- `${WORKSPACE}/skills/knowledge-ops/SKILL.md`
- `~/.claude/rules/skill-trigger-scan.md`（追加触发词行）

## forbidden_paths

- `src/akb2/**`（属其他 step）
- `${WORKSPACE}/skills/akb-search/**`（Plan 2 v3 已覆盖）
- `${AKB2_ROOT}/wiki/**`（保存路径是 staging，不写 wiki）

## blocked_by

（无）

## can_parallel_with

step 2

## required_context

- `${WORKSPACE}/skills/knowledge-ops/SKILL.md`（现有 skill 结构）
- `~/.claude/rules/skill-trigger-scan.md`（触发词表）
- `${AKB2_ROOT}/AKB-2.0-CONTENT-STANDARD.md`（query-result 模板定义，Plan 2 v3 已注册）

## deliverables

- `${WORKSPACE}/skills/knowledge-ops/SKILL.md`（新增 save-query-result 段落）
- `~/.claude/rules/skill-trigger-scan.md`（追加触发词行）

## 实现步骤

1. **新增 skill 段落**
   - WHERE: `${WORKSPACE}/skills/knowledge-ops/SKILL.md` 末尾
   - WHAT: 新增 `## save-query-result` 段落，定义：
     - 触发条件：用户消息含"存一下"/"保存到知识库"/"save to wiki"/"这个值得记住"
     - 提取 query：取用户最近一个问句（最近一条不含触发词的用户消息）
     - 提取 derived_from：取当前对话 akb_search 调用返回的 item_id 列表（智能筛选见 step 4）
     - 确认 confidence：用 AskUserQuestion 让用户选 high/medium/low
     - 生成 slug：基于 query 哈希前 8 位 + 时间戳
     - 写入路径：`${AKB2_ROOT}/staging/candidate/query-result-<slug>.md`
     - 自动校验：subprocess 调 `python3 -m src.akb2.lint.akb2_lint --version v0.3 <file>` + `python3 src/akb2/gate/akb2_gate_check.py check --akb-root ${AKB2_ROOT} --input staging/candidate/<file> --dry-run`
     - 报告结果：lint+gate pass → 提示"候选页已创建，运行 promote 子命令晋升到 wiki/"；fail → 输出 finding 让用户修正
   - VERIFY: `grep -c 'save-query-result' ${WORKSPACE}/skills/knowledge-ops/SKILL.md` >= 3

2. **注册触发词**
   - WHERE: `~/.claude/rules/skill-trigger-scan.md` 高优先级区域
   - WHAT: 追加行 `| 存一下、保存到知识库、save to wiki、这个值得记住 | knowledge-ops | save-query-result 子命令——存对话回答到 staging/candidate/ |`
   - VERIFY: `grep -c '存一下' ~/.claude/rules/skill-trigger-scan.md` >= 1

3. **skill 内置示例**
   - WHERE: `${WORKSPACE}/skills/knowledge-ops/SKILL.md` save-query-result 段落
   - WHAT: 加 1 个完整调用示例（含触发→提取→AskUserQuestion→写文件→报告）
   - VERIFY: `grep -c 'AskUserQuestion' ${WORKSPACE}/skills/knowledge-ops/SKILL.md` >= 1

## 验证

```bash
WORKSPACE="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Claude Code"
grep -c 'save-query-result' "${WORKSPACE}/skills/knowledge-ops/SKILL.md"  # >= 3
grep -c '存一下' ~/.claude/rules/skill-trigger-scan.md                     # >= 1
grep -c 'AskUserQuestion' "${WORKSPACE}/skills/knowledge-ops/SKILL.md"     # >= 1
```

## 非目标

- 不实现 preview/dedup（属 step 3）
- 不实现智能 derived_from 筛选（属 step 4，本步只调 akb_search 全量）
- 不实现 lifecycle 命令（属 step 5）
- 不写 wiki/（save 只到 staging，promote 属 step 2）
