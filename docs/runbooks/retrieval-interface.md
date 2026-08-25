# 检索接口 Runbook（akb2.retrieval）

> 权威文档：描述 `src/akb2/retrieval/`（engine + CLI）的现行为，对着现实现写。
> 文中 bash 代码块里的每条命令都由 `tests/retrieval/test_readme_examples.py`
> 逐条执行并断言真实输出；措辞由 `tests/retrieval/test_doc_wording.py` 锁定
> （T2 禁用词与漂移数值两类扫描）。错误转录放在 text 代码块，不进 bash 块。

## 1. 接口定位

MVP 形态 = 仓内引擎 + CLI，对主链路已升格（promoted）的笔记做关键词检索。

| 模块 | 职责 |
|---|---|
| `src/akb2/retrieval/engine.py` | 检索引擎：promoted 判定、AND 匹配、How 节提取 |
| `src/akb2/retrieval/cli.py` | CLI 入口（`python3 -m akb2.retrieval.cli`）；子命令 `search` / `serve` |
| `src/akb2/retrieval/http_server.py` | 只读 HTTP（token 保护；给 Cindy 插件直连 Mini，不是网页） |

固定字段契约（§5）供后续 MCP 化与端到端链路消费，字段名以本文为准。
HTTP 部署见 `docs/runbooks/retrieval-http.md`。

## 2. 只读保证等级：T1

本接口只读——

- 引擎与 CLI 不写 vault 任何字节，也不发起任何网络请求；
- 该等级由引擎的只读实现与 `tests/retrieval/test_engine.py` 的负例断言
  （staging 候选 / 隔离区 / 未升格笔记永不进入结果）维护；
- 本接口的设计与验证不以刻意构造的输入为对象，对抗性场景不在承诺范围。

## 3. 权威 CLI 命令

前提：在仓库根目录执行；包未安装为 site-package 时以 `PYTHONPATH=src`
前缀运行；vault 根经 env 导出，同时供 §3.5 的自动解析与本节 `--vault-root`
参数使用——`--vault-root` 不做规范化，必须直接是 AKB2.0 子根本身（见 §4）：

```bash
export AKB2_ROOT="/Volumes/AKB2/Obsidian/Praise Space/AKB2.0"
```

### 3.1 查看用法

```bash
PYTHONPATH=src python3 -m akb2.retrieval.cli --help
```

```bash
PYTHONPATH=src python3 -m akb2.retrieval.cli search --help
```

### 3.2 基本检索

```bash
PYTHONPATH=src python3 -m akb2.retrieval.cli search "loop engineering" --limit 5 --output json --vault-root "$AKB2_ROOT"
```

### 3.3 无命中

```bash
PYTHONPATH=src python3 -m akb2.retrieval.cli search "zzz-wubqi-nohit" --vault-root "$AKB2_ROOT"
```

无命中是合法输出：`count` 为零、`results` 为空数组、退出码为 0。

### 3.4 条数上限截断

```bash
PYTHONPATH=src python3 -m akb2.retrieval.cli search "retrieval" --limit 1 --vault-root "$AKB2_ROOT"
```

`--limit` 只截断排序后的结果，不影响匹配与排序本身。

### 3.5 经 env 解析 vault 根（不传 --vault-root）

```bash
PYTHONPATH=src python3 -m akb2.retrieval.cli search "mcp" --limit 3
```

### 3.6 fail-closed：不支持的输出格式

```bash
PYTHONPATH=src python3 -m akb2.retrieval.cli search "loop engineering" --output yaml --vault-root "$AKB2_ROOT"
```

退出码为 2，stderr 提示 invalid choice；CLI 不静默降级到别的格式。

### 3.7 fail-closed：未知子命令

```bash
PYTHONPATH=src python3 -m akb2.retrieval.cli frobnicate
```

退出码为 2；当前子命令是 `search` 与 `serve`。`serve` 的部署见
`docs/runbooks/retrieval-http.md`，不进本文件的 bash 示例块。

## 4. vault 根解析

优先级（高 → 低）：

1. env `AKB2_ROOT`
2. env `AKB2_VAULT_ROOT`
3. env `VAULT_ROOT`
4. `config/vault.toml` 的 `[vault] akb2_root`（指向的卷未挂载时拒绝启动）

`--vault-root` 参数显式指定时优先于上述全部，但**不经过规范化**——CLI 按
字面值直接检索，不做 Obsidian 库根 → AKB2.0 子根的自动拼接。传参必须是
AKB2.0 子根本身（如 `/Volumes/AKB2/Obsidian/Praise Space/AKB2.0`）；传成
Obsidian 库根不会报错，只会静默返回空结果。

两种根形式的自动拼接规范化，只发生在 env/config 解析路径（未传
`--vault-root` 时，即 §3.5 场景）：

- Obsidian 库根（如 `/Volumes/AKB2/Obsidian`）→ 自动拼接 `Praise Space/AKB2.0`
- 已是 AKB2.0 子根 → 原样使用

env 与 config 都无法给出可用根时 fail-closed（退出码为 1），stderr 形如：

```text
RuntimeError: vault.toml akb2_root=/Volumes/AKB2/Obsidian/Praise Space/AKB2.0 指向未挂载的卷 /Volumes/AKB2；拒绝写错盘（fail-closed）。请挂载该卷或设置 VAULT_ROOT/AKB2_ROOT。
```

## 5. 输出契约（JSON）

输出是单个 JSON 对象（`--output json`，当前唯一格式），顶层字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `query` | string | 检索词原样回显 |
| `limit` | integer | 请求的条数上限回显 |
| `count` | integer | 实际返回条数（无命中为零） |
| `results` | array | 结果数组，按 `score` 降序、同分按 `path` 字典序 |

`results` 内单条结果的固定字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `path` | string | vault 相对路径，形如 `wiki/sources/<slug>.md` |
| `title` | string | frontmatter `title`（缺失时回退文件名主干） |
| `how_density` | string | How 节全文；笔记无 How 节时为空串 |
| `how_executability` | array[string] | How 节内 inline 反引号命令，去重保序 |
| `score` | integer | 全部 token 在标题+正文的命中次数之和 |

结构骨架（占位符示意，实际值以运行结果为准）：

```text
{
  "query": "<检索词>",
  "limit": <int>,
  "count": <int>,
  "results": [
    {
      "path": "<string>",
      "title": "<string>",
      "how_density": "<string>",
      "how_executability": ["<string>"],
      "score": <int>
    }
  ]
}
```

## 6. 检索语义

- 检索词按空白分词，全部 token 命中才收录（AND 语义）；大小写不敏感。
- 匹配域 = 标题（frontmatter 提取值）+ 正文全文；frontmatter 其余字段不参与匹配。
- `score` = 各 token 在匹配域出现次数之和，出现多的排前。
- `--limit` 缺省值 10；为零或负数时直接返回空结果；只截断、不参与排序。
- How 节识别两种形态：`### 操作步骤`（新链路）或标题含 `(How 模板)` 的
  `##` 节（历史黄金样张）；都没有时 How 两字段为空，不编造内容。
- How 节内的 code fence 块：块内反引号片段不算可执行命令，块内行也不
  参与下一节标题判定（防止示例代码截断 How 节正文）。

## 7. 样本集口径（哪些笔记会被检出）

只扫描 `wiki/sources/` 下的 `.md`（递归），且 frontmatter 必须同时满足
promoted 四要素（判定全部行首锚定）：

- `page_status: active` 或 `status: active`
- `gate_decision: active`
- `reviewer: akb2-quality-loop-v1`

即使内容与 promoted 笔记完全相同，以下也永不进入结果：

- staging 候选（`status: candidate`；`proposed_gate_decision: active` 因
  行首锚定不被误匹配）
- 隔离区（`staging/quarantine/`，在扫描域之外）
- `wiki/sources/` 内未升格笔记
- `reviewer` 非主链路签名的笔记
- 索引页 `00-Index.md`（收尾段维护，非五闸升格产出）

权威口径：`src/akb2/quality/promote.py` 的 `_flip_frontmatter`；引擎按
只读契约复制常量，不 import quality 模块。

单文件读取失败（权限/解码）时跳过该文件继续扫描：不计入结果、不中断
整体检索。

## 8. fail-closed 行为清单

| 场景 | 行为 |
|---|---|
| 不支持的 `--output` 值 | 退出码为 2（argparse invalid choice），不降级 |
| 未知子命令 / 缺子命令 | 退出码为 2 |
| env 与 config 均无法解析 vault 根 | 退出码为 1，stderr 带 fail-closed 提示（§4） |
| 检索词为空或全空白 | 合法空查询：`count` 为零、退出码为 0 |
| 扫描域 `wiki/sources/` 不存在 | 合法空结果：`count` 为零、退出码为 0 |

## 9. 文档自检（谁在锁这份文档）

- `tests/retrieval/test_readme_examples.py`：提取本文所有 bash 块中以
  `PYTHONPATH=src python3 -m akb2.retrieval.cli` 开头的命令行，逐条交给
  bash 执行并断言真实输出（退出码 / JSON 契约 / fail-closed 行为）；
  场景覆盖表与文档命令行一一对齐——文档改命令而不改测试会红。
- `tests/retrieval/test_doc_wording.py`：T2 禁用词出现次数必须为零；
  漂移数值模式（结果条数/耗时/文件数）命中数必须为零。改本文措辞前
  先读该测试。

维护约定：本文 bash 块内新增命令时，必须同步在 test_readme_examples.py
的 `_SCENARIOS` 增加场景；结果类数字一律写契约语义（如「count 为零」），
不写具体观测值。
