# Step 4: derived_from 智能筛选 + 反向索引

日期：2026-05-17 | blocked_by：step 1 | can_parallel_with：step 3 | SC：SC-4

## 目标

save-query-result skill 不再全量取对话中所有 akb_search 调用，而是按"最近一个用户问句 + 该问句之后 Claude 回答引用过的源页"筛选 derived_from；并写入反向索引 `data/backlinks.jsonl`（每行 `{"source_slug": "...", "referenced_by": ["query-result-slug-1", ...]}`）。

## owned_paths

- `src/akb2/backlinks/`
- `data/backlinks.jsonl`（schema 文档；运行时由脚本生成）
- `${WORKSPACE}/skills/knowledge-ops/SKILL.md`（追加智能筛选段落）

## forbidden_paths

- `src/akb2/lint/**`、`src/akb2/gate/**`、`src/akb2/index/**`、`src/akb2/promotion/**`
- `${WORKSPACE}/skills/akb-search/**`
- `${AKB2_ROOT}/wiki/**`、`${AKB2_ROOT}/staging/**`

## blocked_by

step 1（需要 save-query-result 基线已存在以便追加筛选段落）

## can_parallel_with

step 3（修改 SKILL.md 不同段落）

## required_context

- `${WORKSPACE}/skills/knowledge-ops/SKILL.md`（step 1 交付的 save-query-result 段落）
- `${AKB2_ROOT}/wiki/synthesis/`（query-result 页面所在目录，反向索引来源）

## deliverables

- `src/akb2/backlinks/__init__.py`
- `src/akb2/backlinks/update_backlinks.py`（CLI 工具，支持 --rebuild）
- `data/backlinks.jsonl`（文件存在即可，schema 由脚本运行时生成；本步初始为空）
- `${WORKSPACE}/skills/knowledge-ops/SKILL.md`（新增 derived_from 筛选段落 + backlinks 引用）

## 实现步骤

1. **创建包结构**
   - WHERE: `src/akb2/backlinks/__init__.py`
   - WHAT: 空文件
   - VERIFY: `test -f src/akb2/backlinks/__init__.py`

2. **实现 update_backlinks.py**
   - WHERE: `src/akb2/backlinks/update_backlinks.py`
   - WHAT: argparse 设计：
     - `--wiki-root`（必填）：扫描该目录下所有 query-result 页面
     - `--backlinks-file`（默认 `data/backlinks.jsonl`）：输出反向索引文件路径
     - `--rebuild`：全量重算（清空再写）；不传则增量更新
     - `--add-entry <query-result-slug>`：单条添加（save-query-result 完成后调用）
     - 输出格式：每行 JSON `{"source_slug": "<src>", "referenced_by": ["<qr-1>", "<qr-2>"]}`
     - 算法：读所有 query-result 页 frontmatter derived_from 数组，反转为 source→list 映射
   - VERIFY: `python3 src/akb2/backlinks/update_backlinks.py --help` exit 0

3. **创建数据文件占位**
   - WHERE: `data/backlinks.jsonl`
   - WHAT: 空文件（运行时由脚本写入）；同目录建 `data/README.md` 说明 schema
   - VERIFY: `test -f data/backlinks.jsonl`

4. **追加 skill 智能筛选段落**
   - WHERE: `${WORKSPACE}/skills/knowledge-ops/SKILL.md` save-query-result 段落内"提取 derived_from"步骤
   - WHAT: 替换原"全量取 akb_search item_id"为：
     - 定位最近一个不含触发词的用户消息位置
     - 取该位置之后到当前位置之间所有 akb_search 调用的 item_id
     - 过滤掉 Claude 回答中没引用过的 item_id（grep Claude 回答正文检查 slug 出现）
     - 兜底：若筛选结果为空，提示用户手动输入或回退到全量
   - VERIFY: `grep -c '最近一个不含触发词的用户消息\|筛选' ${WORKSPACE}/skills/knowledge-ops/SKILL.md` >= 1

5. **追加 backlinks 触发段落**
   - WHERE: `${WORKSPACE}/skills/knowledge-ops/SKILL.md` save-query-result 段落"自动校验"步骤之后
   - WHAT: 新增"写入 backlinks"步骤：lint+gate pass 后调 `python3 src/akb2/backlinks/update_backlinks.py --wiki-root ${AKB2_ROOT}/wiki --add-entry <new-slug>` 增量更新
   - VERIFY: `grep -c 'backlinks' ${WORKSPACE}/skills/knowledge-ops/SKILL.md` >= 2

## 验证

```bash
WORKSPACE="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Claude Code"
test -f src/akb2/backlinks/__init__.py
test -f src/akb2/backlinks/update_backlinks.py
test -f data/backlinks.jsonl
python3 src/akb2/backlinks/update_backlinks.py --help                           # exit 0
grep -c 'backlinks' "${WORKSPACE}/skills/knowledge-ops/SKILL.md"                # >= 2
grep -c '最近一个不含触发词的用户消息\|筛选' "${WORKSPACE}/skills/knowledge-ops/SKILL.md"  # >= 1
```

## 非目标

- 不接 LLM 语义判断"引用了哪些源页"（本期用 grep 字符串匹配）
- 不在 wiki/ 页面正文内嵌入 backlinks（只维护独立 jsonl 文件，避免污染页面）
- 不做反向索引的 UI 展示（属未来）
- 不在 save 之外的场景自动更新（promote/update/deprecate 触发更新属 step 5）
