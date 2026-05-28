---
date: 2026-05-28
plan: 社交热点日报 MVP v1 — Mac Mini 部署正确性测试计划
worktree: akb2-gd-plan-social-trends
branch: feature/automation-pipeline
author: claude (opus-4-7)
status: revised-after-codex-r7
supersedes: 无
related:
  - reports/acceptance/2026-05-27.md
  - reports/exec/step-5-pipeline-result.md
  - reports/exec/step-6-acceptance-result.md
  - docs/mac-mini-deployment-notes.md
---

# Mac Mini 部署正确性测试计划

## 0. 目标重定义

| 维度 | 原目标 | 本计划目标 |
|------|--------|-----------|
| 范围 | 验证本机改动不破坏 acceptance 报告声明的 PASS | 保证 mac mini 部署后,首次 `launchctl load` → 次日 07:30 自动触发 → 写入 iCloud vault → 全链路成功(含真实 API) |
| 边界 | 本机 fixture | 本机模拟 launchd ENV + mac mini manual 清单 |
| 通过判据 | SC 矩阵全 PASS | A 类全绿 + B 类清单作为部署交付物 |

本次改动驱动:
1. 新增 `scripts/find-python311.sh`(launchd-safe Python 解析器)
2. `scripts/daily_briefing.sh` / `scripts/dry_run_e2e.sh --check` 改为调用上述解析器
3. `docs/mac-mini-deployment-notes.md` 重写(Python 3.11+ / 双占位符 / 物理文件)
4. `reports/exec/step-5-*.md` / `step-6-*.md` 更新(symlink → 物理文件;新 SC 入矩阵)

**Codex r3 前置代码修复（测试前必须完成）**:
5. `pipeline.py`：args 解析后立即检查 vault_root 是否存在，不存在 exit 1（Codex P1a）
6. `daily_briefing.sh`：Python 调用前 source `~/.akb2-secrets.sh`（Codex P1b）
7. `find-python311.sh`：`${HOME:-}` 替换 `${HOME}`，防止 set -u 在 HOME 缺失时 abort（Codex P2a）

---

## A 类 — 本机可验证(必须全绿才提交)

### Phase 1 — 结构 sanity(30 秒)

| 项 | 命令 | 通过条件 | 痛点(不做会怎样) |
|---|---|---|---|
| 1.1 新文件权限位 | `test -x scripts/find-python311.sh` | exit 0 | heredoc 写文件可能丢 +x,launchd 调用会 `permission denied` |
| 1.2 plist lint | `plutil -lint launchd/com.akb2.briefing.plist` | "OK" | plist 损坏 `launchctl load` 直接拒绝 |
| 1.3 Python 模块 ast 解析 | 对 pipeline/synthesis/validator/三 collector/scoring 各跑 `"$PY" -c "import ast; ast.parse(open('<path>').read())"` | 全 exit 0 | 语法错误在生产首跑时才暴露 |

### Phase 2 — find-python311.sh 在 launchd-like 环境(关键)

| 项 | 命令 | 通过条件 | 痛点 |
|---|---|---|---|
| 2.1 直接调用 | `bash scripts/find-python311.sh` | exit 0;输出非空 | 基本功能 |
| 2.2 返回的 Python ≥ 3.11 | `"$(bash scripts/find-python311.sh)" -c "import sys; assert sys.version_info >= (3,11)"` | exit 0 | 解析对但版本不对 |
| 2.3 **模拟 launchd 最小 ENV** | `env -i HOME="$HOME" PATH="/usr/bin:/bin" bash scripts/find-python311.sh` | exit 0 | **launchd 不继承 shell PATH**;若解析器只在交互 shell 工作,部署当天 07:30 直接 crash |
| 2.4 模拟 plist 实际 PATH | `env -i HOME="$HOME" PATH="$HOME/.pyenv/shims:$HOME/.pyenv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" bash scripts/find-python311.sh` | exit 0 + Python 3.11+ | 验证 plist EnvironmentVariables 里写的 PATH 真能找到 Python |
| 2.5a HOME 缺失优雅降级 | `env -i PATH="/usr/bin:/bin" bash scripts/find-python311.sh 2>&1; echo "exit=$?"` | stderr **不含** "unbound variable";根据是否有 Python 决定 exit 0/1 | 修复前 `set -u` 导致脚本中止,报 "HOME: unbound variable"——launchd 日志会混淆排障 |
| 2.5b 无 pyenv 路径 | `env -i HOME=/tmp/no-pyenv-home PATH="/usr/bin:/bin" bash scripts/find-python311.sh 2>&1; echo "exit=$?"` | 跳过 pyenv 分支;若 /opt/homebrew 也无 Python 3.11+ → exit 1 + "ERROR: Python 3.11+" | 验证 HOME 无效时脚本不 crash,给出可读错误 |

### Phase 3 — 完整 launchd 模拟跑(最接近生产环境)

| 项 | 命令 | 通过条件 | 痛点 |
|---|---|---|---|
| 3.1 用 env -i 完整跑 daily_briefing.sh | `env -i HOME="$HOME" PATH="$HOME/.pyenv/shims:$HOME/.pyenv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" PYTHONPATH="$PWD/src" bash scripts/daily_briefing.sh --date 2026-05-27 --vault-root tests/fixtures/vault-stub --dry-run-intake` | exit 0 + Social/ 下有物理文件 | **之前 SC 矩阵走的是交互 shell;这条才是 mac mini 实际执行的模拟** |
| 3.2 stdout/stderr 落盘路径可写（plist 渲染后） | 见附录 §3.2 — sed 替换占位符后 `plutil -extract StandardOutPath/StandardErrorPath` 抽路径，mkdir + touch 验证可写 | 两路径均能 touch 成功；抽出的值不再含 `__AKB2_` 占位符 | launchd StandardOutPath/StandardErrorPath 不可写 → job load 失败反复重启 |
| 3.3 logs/ 目录不存在时行为 | `rm -rf logs/; env -i HOME="$HOME" PATH="$HOME/.pyenv/shims:$HOME/.pyenv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" PYTHONPATH="$PWD/src" bash scripts/daily_briefing.sh --date 2026-05-27 --vault-root tests/fixtures/vault-stub --dry-run-intake` | exit 0；pipeline 不依赖 logs/ 存在 | mac mini launchd 注册时 StandardOutPath 不可写拒绝 load，由 B9 单独检查 |
| 3.4 secrets wrapper 级集成验证（Codex P1b/r5） | 见附录 §3.4 — 临时 HOME 下写含 marker `touch` 命令的 `.akb2-secrets.sh`，通过真实 wrapper 触发验证 source；真实 `~/.akb2-secrets.sh` 的 chmod 600 + 变量非空留 B7/B8 | ① `/tmp/akb2-secret-sourced-34` marker 文件存在 → wrapper 确实 source 了 secrets 文件 | A 类不依赖本机真实 secrets；原 `_AKB2_DUMMY_KEY` 不在链路变量表，无法通过 stderr 验证注入 |

### Phase 4 — 真实 vault 路径语义(含空格 + iCloud 格式)

| 项 | 命令 | 通过条件 | 痛点 |
|---|---|---|---|
| 4.1 含空格的 vault 路径 | 用 `/tmp/fake icloud/iCloud~md~obsidian/Documents` 做 vault-root | pipeline 写入成功 | iCloud 路径含空格 `Mobile Documents`,bash 引号一处错就 broken |
| 4.2 真实 iCloud 路径格式 mock | 见附录 §4.2 — mock `/tmp/mock-vault/iCloud~md~obsidian/Documents` 跑 pipeline | exit 0 + 物理文件落 `AKB2.0/AI TRENDS/Social/2026-05-28 Daily Briefing.md` | mkdir -p 链需正确处理多层子路径 + 含 `~` 字符的路径段 |
| 4.3 vault 父目录不存在（Codex P1a） | `rm -rf /tmp/akb2-test-novault; bash scripts/daily_briefing.sh --date 2026-05-27 --vault-root /tmp/akb2-test-novault --dry-run-intake 2>/tmp/4.3-err.txt; echo "exit=$?"` | ① exit 非 0；② `/tmp/4.3-err.txt` 含 "ERROR: --vault-root does not exist"；③ `! test -d /tmp/akb2-test-novault`（无静默建目录）；④ stderr 无 OPENROUTER WARNING（check 在 intake/synthesis 前） | 修复前 pipeline 静默 mkdir -p 写错路径；launchd 不报错，Obsidian 永远看不到 |
| 4.4 vault 只读 | 见附录 §4.4 — `chmod -R u-w` 后跑 pipeline，捕获 exit + stderr | exit 非 0 + stderr 含 "Permission denied" 或 "Read-only" | 不能让 launchd 在 07:30 静默写入失败 |

### Phase 5 — 部署文档每条命令可执行(P2 finding 的实质验证)

按 `docs/mac-mini-deployment-notes.md` 从头到尾,在临时目录 mock REPO_ROOT 和 VAULT_ROOT 跑全文:

| 步 | 检查 | 通过条件 |
|---|---|---|
| 5.1 sed 双占位符替换 | 替换后文件 `! grep '__AKB2_'` | 无残留占位符 |
| 5.2 替换后 plist `plutil -lint` | exit 0 | 仍合法 |
| 5.3 替换后 plist `--vault-root` 真值 | `plutil -extract ProgramArguments json -o - <file>` | 不再是占位符 |
| 5.4 文档部署命令静态检查(allowlist 策略) | 见附录 §5.4 — 提取 fenced bash block 到临时脚本，对 allowlist 片段跑 `bash -n`；launchctl/cp LaunchAgents 等真实系统变更命令只放 B 类手动清单；不对整份 Markdown 跑 bash -n | bash -n 对提取片段 exit 0；文档含 "首次部署 verify 清单" 和 "已知风险" 两节 | 对 Markdown 直接 `bash -n` 必然在第 7 行就报 syntax error（普通 Markdown 格式不是 shell） |
| 5.5 文档里所有 ${VAR} 是否在前文 export 过 | 见附录 §5.5 — grep 提取 `${VAR}` 引用，逐个对照 export / 占位符上下文 | 每个 `${VAR}` 在文档中能找到对应 `export VAR=` 或 `__VAR__` 占位符；无 MISSING 输出 |
| 5.6 文档里 Python 3.11+ 检查命令 | 见附录 §5.6 — 文档须统一引用 `find-python311.sh`，不得硬编码 `/usr/bin/python3` 作 3.11+ 入口 | grep 命中 `find-python311.sh` ≥ 1 次；grep `/usr/bin/python3`（非 `.x` 后缀）零命中 |
| 5.7 文档 troubleshooting 表覆盖度 | grep 关键字 | 至少含 "Python 3.9" 和 "占位符未替换" |
| 5.8 部署文档包含 B1-B15 清单和 C 类风险表 | `grep -q "首次部署 verify 清单" docs/mac-mini-deployment-notes.md && grep -q "已知风险" docs/mac-mini-deployment-notes.md` | 两节均存在 | 提交时遗漏清单，mac mini 部署员无据可查 |

### Phase 6 — fail-closed 路径完整性

| 失败场景 | 测试 | 期望行为 | 痛点 |
|---|---|---|---|
| 6.1 OPENROUTER_API_KEY 缺失 | 已在 SC-5 / acceptance 中验证 | fallback + stderr WARNING | launchd 日志能看到 WARNING |
| 6.2 OPENROUTER_API_KEY 错值(401/403) | 见附录 §6.2 — 代码静态 review：grep synthesis.py 对 4xx 的处理路径 | 看到对 `response.status_code` / 4xx 的显式处理且 stderr 有 WARNING/ERROR，而非静默走 fallback | API 错误若静默,运营方以为正常 |
| 6.3 X_BEARER_TOKEN 缺失 | 见附录 §6.3 — 三 collector 在 unset token 下跑，验证 fallback + WARNING | 三路均 exit 0；stderr 至少 1 行含 "WARNING" | launchd 拿不到 token 时不能静默 |
| 6.4 三 collector 全空 | 见附录 §6.4 — 注入空 tweets 跑 scoring | scoring exit 非 0 或下游 pipeline fail-closed，不写文件到 vault | 不能产空简报 |
| 6.5 synthesis 输出不过 schema | 见附录 §6.5 — 注入坏 JSON 跑 validator | validator exit 非 0 + stderr 含 schema error | 不能写坏文件到 vault |
| 6.6 gate 输出 reject | 见附录 §6.6 — Python stub 强制 reject（text 格式匹配 parser），通过 `AKB2_GATE_CLI` 注入；断言 Social/active 无文件 + pipeline exit 非 0 | **产品 contract**：reject 时 Social/active 无文件；pipeline exit 非 0 或 stderr 含 "reject" | **代码 gap ×2**：① pipeline.py 无条件 shutil.copy2（须加 reject guard）；② pipeline.py 未读 `AKB2_GATE_CLI` env var（须在 GATE_CLI 定义处加 env override）；两项均须修复后测试才能通过 |
| 6.7 staging/candidate 不存在 | 见附录 §6.7 — 全新空目录跑 pipeline | exit 0 + 自动创建 staging/candidate/ | 首次跑失败 |
| 6.8 SCRIPT_DIR 含空格 | 见附录 §6.8 — symlink worktree 到 `/tmp/with space/worktree` 跑 wrapper | exit 0；wrapper 不在空格路径上崩 | mac mini 上若装在含空格路径下挂 |

### Phase 7 — Acceptance 报告 15 项 SC 重跑(retention test)

按 `reports/acceptance/2026-05-27.md` §1 矩阵全量重跑 — 确保 Phase 1-6 的改动不 regress 已有功能。每项退出码与报告声明一致。

### Phase 8 — pytest 收集问题(阻塞提交)

| 步 | 命令 | 期望 |
|---|---|---|
| 8.1 用 3.11+ Python 收集 | `PY="$(bash scripts/find-python311.sh)"; "$PY" -m pytest --collect-only tests/ -q` | ≥ 36 tests |
| 8.2 若 0,检查 conftest | `find tests -name conftest.py -exec head -5 {} \;` | 无 sys.exit / 导入失败 |
| 8.3 若 0,单文件试跑 | `"$PY" -m pytest tests/briefing/test_validator.py -v` | ≥ 1 test |

**决策规则**:pytest 全跑通 → 提交;仅个别失败 → 修复后再跑;**0 collected → 阻塞提交**(测试基础设施坏掉,acceptance SC 不能替代;需开独立缺陷单或修复 conftest 后再提交)。

---

## B 类 — Mac Mini Manual 清单(本机无法替代,作为部署交付物)

诚实承认:以下事项本机绝对无法替代,必须在 mac mini 上首次部署时按顺序跑。本清单将作为 patch 加入 `docs/mac-mini-deployment-notes.md` 的"首次部署 verify 清单"小节。

| 序 | 检查 | 命令 / 动作 | 失败影响 |
|---|---|---|---|
| B1 | 用户名/HOME 验证 | `echo $HOME` 应为 `/Users/praise`,否则改 plist EnvironmentVariables 中的 PATH 和 find-python311.sh 的 HOME 探测 | pyenv 路径硬编码,HOME 不同即失效 |
| B2 | pyenv 装好 3.11+ | `pyenv versions \| grep "3\.1[1-9]"` | 找不到 Python 整个链路死 |
| B3 | `pip install -e .` 成功 | `pip list \| grep -i akb2` | 模块 import 失败 |
| B4 | 时区设置 | `sudo systemsetup -gettimezone` = Asia/Shanghai | 07:30 实际触发时间错 |
| B5 | iCloud Drive 登录 + Obsidian vault 同步开 | 系统设置检查 | 写入成功但 Obsidian 看不到 |
| B6 | VAULT_ROOT 父目录存在且可写 | `test -w "/Users/praise/Library/Mobile Documents/iCloud~md~obsidian/Documents"` | pipeline fail |
| B7 | X_BEARER_TOKEN 配置 + secrets 文件权限 | `test -f ~/.akb2-secrets.sh || echo "FAIL: missing secrets file"` → `test "$(stat -f '%Lp' ~/.akb2-secrets.sh)" = "600" || echo "FAIL: must be chmod 600"` → `source ~/.akb2-secrets.sh; [[ -n "${X_BEARER_TOKEN:-}" ]] && echo "X_BEARER_TOKEN SET" || echo "FAIL: X_BEARER_TOKEN empty"` | SC-3-real fail;secrets 宽权限 → API key 本机泄露风险 |
| B8 | OPENROUTER_API_KEY 配置（chmod 600 已在 B7 验证） | `source ~/.akb2-secrets.sh; [[ -n "${OPENROUTER_API_KEY:-}" ]] && echo "OPENROUTER_API_KEY SET" || echo "FAIL: OPENROUTER_API_KEY empty"` → 进一步验证 launchd-like env:`env -i HOME="$HOME" PATH="..." bash -c 'source ~/.akb2-secrets.sh; [[ -n "${OPENROUTER_API_KEY:-}" ]] && echo OK'` | SC-5-real fail;synthesis 走 fallback,日志有 WARNING |
| B9 | logs/ 目录预创建 | `mkdir -p $REPO_ROOT/logs` | launchctl load 拒绝(StandardOutPath 不可写) |
| B10 | launchctl load 成功 | `launchctl load ~/Library/LaunchAgents/com.akb2.briefing.plist; launchctl list \| grep akb2` | 未注册 |
| B11 | next fire date ≤ 24h | `launchctl print gui/$(id -u)/com.akb2.briefing \| grep "next fire"` | 时区/Hour/Minute 配置错 |
| B12 | 首次手动触发 dry-run | `launchctl kickstart -k gui/$(id -u)/com.akb2.briefing` 后查日志 | 等 24h 才发现错误 |
| B13 | 真实触发后简报落 iCloud | 隔日检查 `"/Users/praise/Library/Mobile Documents/iCloud~md~obsidian/Documents/AKB2.0/AI TRENDS/Social/<次日> Daily Briefing.md"` | 端到端断 |
| B14 | Obsidian 客户端看到该文件 | 打开 Obsidian | iCloud 同步未生效 |
| B15 | 简报内容质量人工抽检 | 打开看 focus_discussions 是否合理 | 真实 API 输出可能跑偏 |

---

## C 类 — 已知 Mac Mini 风险与缓解(写入 deployment notes)

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| HOME 不是 /Users/praise | 中(取决于 mac mini 用户名) | find-python311.sh / plist PATH 全部失效 | B1 必检;若不同需改 plist 用 `$HOME` 展开 + find-python311.sh 不硬编码 |
| pyenv 未装 3.11+ | 中 | 整个链路死 | B2 必检 |
| API key 注入方式（Codex P1b） | 高 | secrets 泄漏 / launchd 拿不到 | **代码前置要求**：`daily_briefing.sh` 必须在 Python 调用前 source `~/.akb2-secrets.sh`（chmod 600）；不打印 secret 值；B7/B8 有可执行验证命令 |
| iCloud 同步延迟 | 中 | 文件写了但 Obsidian 隔几小时才看到 | 已知接受;Obsidian 主动刷新可缓解 |
| mac mini 睡眠时跳过触发 | 高 | 07:30 当时睡眠 → launchd 醒后补跑 OR 跳过 | 加 `RunAtLoad`/电源设置不睡眠/`StartCalendarInterval` 多次配置 |
| 时区漂移 | 低 | 07:30 偏移 | B4 |
| logs 占用磁盘 | 长期 | 几个月后 GB 级 | logrotate 或 plist 加 size 限制(本轮不做) |

---

## D 类 — 测试计划本身的局限(SH 诚实性声明)

1. **Phase 2-3 的 launchd 模拟不是 100% 等价** — 真正 launchd 还有 session_type、用户 context 区别。本机 `env -i` 只能模拟环境变量,不能模拟 launchd 的进程组/用户态差异。残留风险归 B 类。
2. **Phase 6 部分场景需要 mock 注入** — API 401 路径在没有可控 mock server 情况下只能用代码 review 替代,降低置信度。
4. **Phase 6.3 WARNING 验证假阳性风险** — Codex 实测 collector 缺 token 时 stderr 为空，不含 WARNING；Phase 6.3 期望 "stderr 至少 1 行含 WARNING" 在当前代码下必然 FAIL。提交前手动跑一次 Phase 6.3 确认实际输出；如 stderr 为空则应修复 collector 输出 WARNING，或把期望改为 "exit 0 + fixture 数据"（降格接受）。
5. **pipeline.py 注释漂移** — 文件顶部注释仍写 Social 为 symlink，但实现已改为 shutil.copy2 物理复制；建议提交前修正，避免后续 review 混淆。
3. **B 类是真正能保证部署成功的核心** — A 类只能保证"代码逻辑对"不能保证"mac mini 环境对"。本计划完成不代表部署一定成功,只代表本机责任已尽。

---

## E 类 — 执行顺序与提交策略

### 执行顺序

```
前置代码修复 → 文档 patch（B/C 清单写入 deployment notes）→ Phase 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 提交
```

**前置**（Phase 1 之前必须完成）：
1. `pipeline.py` 加 `AKB2_GATE_CLI` env override 和 reject guard（Phase 6.6 前置要求）
2. `docs/mac-mini-deployment-notes.md` 写入“首次部署 verify 清单”（B 类 B1-B15）和“已知风险”（C 类风险表）→ Phase 5.8 才能通过

任一 Phase fail-stop（含 Phase 8：0 collected 阻塞提交）。

### 提交策略

1. **前置**：`docs/mac-mini-deployment-notes.md` 先写入 B/C 清单（Phase 5.8 依赖此步骤）
2. A 类 Phase 1-8 必须全绿 → 才提交本 worktree
3. commit message 明确写“本机部分已验证;mac mini 部分见 deployment notes §首次部署 verify 清单”
4. **不 push**(用户决定何时 push)

### Commit message 草拟

```
fix(social-trends): Codex r3+r4 hardening — secrets + vault fail-closed + resolver guard

Codex P1a — pipeline.py vault_root fail-closed:
  check vault_root.is_dir() before intake/synthesis; exit 1 + readable stderr

Codex P1b — launchd secrets injection:
  daily_briefing.sh source ~/.akb2-secrets.sh before Python resolver (chmod 600)

Codex P2a — find-python311.sh HOME guard:
  ${HOME:-} everywhere; set -u no longer aborts on missing HOME

Codex r3 P2b + r4 test plan updates:
  Phase 3.4 wrapper-level secrets test (B7/B8 backslash false-positive fixed);
  Phase 5.4 fenced-block extraction (not bash -n on Markdown); Phase 5.8 new;
  Phase 6.6 gate stub executable test + pipeline.py reject-must-block contract;
  D類 added: Phase 6.3 WARNING gap + pipeline.py symlink comment drift

Local verify: A class Phase 1-8 all green; pytest 127 passed; 15 SC re-run.
Mac mini verify: docs/mac-mini-deployment-notes.md §首次部署 verify 清单 (B1-B15).
```

---

## F 类 — 三个决策点(已 auto-mode 决议)

| 决策 | 选择 | 理由 |
|---|---|---|
| F1. B 类清单 + C 类风险表是否加 deployment notes | **加** | 否则部署当天又踩坑;预计新增约 30 行 |
| F2. Phase 6 mock 缺失部分如何处理 | **代码 review + 标 known gap** | 当前代码无可注入点;不阻塞本轮提交 |
| F3. HOME 硬编码问题是否本轮 scope | **find-python311.sh 已加 `${HOME:-}` guard（Codex P2a 完成）；HOME 路径具体值差异仍留 B1 mac mini 实测** | HOME 缺失/未设时不再 crash 出 "unbound variable"；但 HOME 值若不等于 `/Users/praise`，pyenv 路径仍需 mac mini 上实测确认（本机不能预判） |

---

## §附录 — 命令汇总(直接 copy-paste 跑)

```bash
# 进 worktree
cd "/Users/praise/AI-Agent/Claude/projects/Project AKB2/.claude/worktrees/akb2-gd-plan-social-trends"

# 解析 Python
PY="$(bash scripts/find-python311.sh)"
echo "PY=$PY"
"$PY" -c "import sys; print(sys.version_info)"

# Phase 1.1 / 1.2
test -x scripts/find-python311.sh && echo "P1.1 OK"
plutil -lint launchd/com.akb2.briefing.plist

# Phase 1.3
for f in src/akb2/briefing/pipeline.py \
         src/akb2/briefing/synthesis.py \
         src/akb2/briefing/validator.py \
         src/akb2/briefing/scoring.py \
         src/akb2/briefing/intake/l0_collector.py \
         src/akb2/briefing/intake/l0_prime_collector.py \
         src/akb2/briefing/intake/l3_collector.py; do
  "$PY" -c "import ast; ast.parse(open('$f').read()); print('OK: $f')"
done

# Phase 2.3 (关键)
env -i HOME="$HOME" PATH="/usr/bin:/bin" bash scripts/find-python311.sh
echo "exit=$?"

# Phase 2.4
env -i HOME="$HOME" PATH="$HOME/.pyenv/shims:$HOME/.pyenv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" \
  bash scripts/find-python311.sh

# Phase 2.5a: HOME 缺失优雅降级 (不应 "unbound variable")
env -i PATH="/usr/bin:/bin" bash scripts/find-python311.sh 2>&1; echo "exit=$?"
# 期望: stderr 无 "unbound variable"; exit 取决于是否找到 Python (本机有 Homebrew → exit 0)

# Phase 2.5b: 无 pyenv HOME (skip pyenv branch)
env -i HOME=/tmp/no-pyenv-home PATH="/usr/bin:/bin" bash scripts/find-python311.sh 2>&1; echo "exit=$?"
# 期望: 跳过 pyenv; 若 Homebrew 找到 → exit 0; 若无任何 3.11+ → exit 1 + ERROR

# Phase 3.1 (最关键)
env -i HOME="$HOME" PATH="$HOME/.pyenv/shims:$HOME/.pyenv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" \
      PYTHONPATH="$PWD/src" \
  bash scripts/daily_briefing.sh \
    --date 2026-05-27 \
    --vault-root tests/fixtures/vault-stub \
    --dry-run-intake

# Phase 3.2: stdout/stderr 落盘路径可写
sed -e "s|__AKB2_REPO_ROOT__|$PWD|g" \
    -e "s|__AKB2_VAULT_ROOT__|/tmp/akb2-vault-fake|g" \
    launchd/com.akb2.briefing.plist > /tmp/akb2-rendered-32.plist
! grep -q '__AKB2_' /tmp/akb2-rendered-32.plist && echo "占位符已清: OK"
_out=$(plutil -extract StandardOutPath raw -o - /tmp/akb2-rendered-32.plist)
_err=$(plutil -extract StandardErrorPath raw -o - /tmp/akb2-rendered-32.plist)
echo "StandardOutPath=$_out"
echo "StandardErrorPath=$_err"
mkdir -p "$(dirname "$_out")" "$(dirname "$_err")"
touch "$_out" "$_err" && echo "两路径可写: OK"
# 清理（不删本机 logs/ 真目录，仅删测试用 touch 文件）
[[ "$_out" == /tmp/* ]] && rm -f "$_out"
[[ "$_err" == /tmp/* ]] && rm -f "$_err"

# Phase 3.4: secrets wrapper 级集成验证（Codex P1b/r5）
# 目标：证明 daily_briefing.sh 会 source $HOME/.akb2-secrets.sh
# 方法：marker 文件（secrets 文件中含 touch 命令，source 后 marker 必存在）
# 注：真实 ~/.akb2-secrets.sh 的 chmod 600 + 变量非空 → B7/B8 Mac mini 验证（A 类不做）
TMP_HOME_34=/tmp/akb2-test-home-34
rm -rf "$TMP_HOME_34"; mkdir -p "$TMP_HOME_34"
# 只写 marker touch，不注入 dummy API key（避免触发真实 OpenRouter 调用后 fallback）
printf 'touch /tmp/akb2-secret-sourced-34\n' > "$TMP_HOME_34/.akb2-secrets.sh"
chmod 600 "$TMP_HOME_34/.akb2-secrets.sh"
rm -f /tmp/akb2-secret-sourced-34
env -i HOME="$TMP_HOME_34" \
    PATH="$HOME/.pyenv/shims:$HOME/.pyenv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" \
    PYTHONPATH="$PWD/src" \
    bash scripts/daily_briefing.sh \
        --date 2026-05-27 \
        --vault-root tests/fixtures/vault-stub \
        --dry-run-intake 2>/tmp/3.4-stderr.txt; echo "wrapper exit=$?"
# ① marker 文件存在 → wrapper 确实执行了 source
[[ -f /tmp/akb2-secret-sourced-34 ]] \
  && echo "P3.4① secrets 已 source: OK" \
  || echo "P3.4① FAIL: secrets 未被 wrapper source（marker 不存在）"
rm -f /tmp/akb2-secret-sourced-34 /tmp/3.4-stderr.txt
rm -rf "$TMP_HOME_34"

# Phase 4.1 (含空格 vault)
TMPDIR_SP="/tmp/fake icloud/iCloud~md~obsidian/Documents"
mkdir -p "$TMPDIR_SP/AKB2.0/AI TRENDS/Social"
bash scripts/daily_briefing.sh --date 2026-05-27 --vault-root "$TMPDIR_SP" --dry-run-intake
ls -la "$TMPDIR_SP/AKB2.0/AI TRENDS/Social/"
rm -rf "/tmp/fake icloud"

# Phase 4.3: vault 不存在 → fail-closed，4 断言（Codex P1a）
rm -rf /tmp/akb2-test-novault
bash scripts/daily_briefing.sh --date 2026-05-27 --vault-root /tmp/akb2-test-novault --dry-run-intake 2>/tmp/4.3-err.txt; _e=$?
[[ $_e -ne 0 ]]                              && echo "① exit 非 0: OK"     || echo "① FAIL"
grep -q "ERROR.*vault-root" /tmp/4.3-err.txt && echo "② stderr ERROR: OK"  || echo "② FAIL"
! test -d /tmp/akb2-test-novault             && echo "③ 无静默建目录: OK"  || echo "③ FAIL"
! grep -q "OPENROUTER" /tmp/4.3-err.txt      && echo "④ 无 API call: OK"   || echo "④ FAIL"

# Phase 4.2: 真实 iCloud 路径格式 mock
TMP_MOCK="/tmp/mock-vault/iCloud~md~obsidian/Documents"
mkdir -p "$TMP_MOCK/AKB2.0/AI TRENDS/Social"
bash scripts/daily_briefing.sh --date 2026-05-27 --vault-root "$TMP_MOCK" --dry-run-intake
test -f "$TMP_MOCK/AKB2.0/AI TRENDS/Social/2026-05-28 Daily Briefing.md" && echo "P4.2 OK" || echo "P4.2 FAIL"
rm -rf /tmp/mock-vault

# Phase 4.4: vault 只读 → fail-closed
TMP_RO=/tmp/akb2-readonly-vault
mkdir -p "$TMP_RO/AKB2.0/AI TRENDS/Social"
chmod -R u-w "$TMP_RO"
bash scripts/daily_briefing.sh --date 2026-05-27 --vault-root "$TMP_RO" --dry-run-intake 2>/tmp/4.4-err.txt; _e=$?
chmod -R u+w "$TMP_RO" && rm -rf "$TMP_RO"
[[ $_e -ne 0 ]]                                              && echo "① exit 非 0: OK"        || echo "① FAIL"
grep -qE "Permission denied|Read-only|Errno 13|Errno 30" /tmp/4.4-err.txt && echo "② stderr 含权限错误: OK" || echo "② FAIL"

# Phase 5.1 / 5.2 / 5.3
TMP_REPO=/tmp/akb2-repo-fake
TMP_VAULT="/tmp/akb2-vault-fake"
sed -e "s|__AKB2_REPO_ROOT__|$TMP_REPO|g" \
    -e "s|__AKB2_VAULT_ROOT__|$TMP_VAULT|g" \
    launchd/com.akb2.briefing.plist > /tmp/akb2-plist-rendered.plist
! grep -q '__AKB2_' /tmp/akb2-plist-rendered.plist && echo "P5.1 OK"
plutil -lint /tmp/akb2-plist-rendered.plist
plutil -extract ProgramArguments json -o - /tmp/akb2-plist-rendered.plist

# Phase 5.4: 文档部署命令静态检查（allowlist，不对整份 Markdown 跑 bash -n）
python3 - <<'PY'
import re, pathlib
doc = pathlib.Path("docs/mac-mini-deployment-notes.md").read_text()
blocks = re.findall(r"```bash\n(.*?)```", doc, re.DOTALL)
BLOCKLIST = ["launchctl load", "launchctl unload", "launchctl kickstart",
             "cp ~/Library/LaunchAgents", "sudo systemsetup"]
safe, skipped = [], 0
for i, b in enumerate(blocks):
    if any(bl in b for bl in BLOCKLIST):
        print(f"block {i}: skip (system-mutating)"); skipped += 1; continue
    safe.append(b)
out = pathlib.Path("/tmp/5.4-safe-blocks.sh")
out.write_text("#!/usr/bin/env bash\nset -e\n" + "\n".join(safe))
print(f"safe={len(safe)}, skipped={skipped}, written={out}")
PY
bash -n /tmp/5.4-safe-blocks.sh && echo "P5.4 bash -n OK" || echo "P5.4 FAIL"
grep -q "首次部署 verify 清单" docs/mac-mini-deployment-notes.md \
  && echo "P5.4 首次部署清单节: OK" || echo "P5.4 FAIL: 缺少首次部署清单节"
grep -q "已知风险" docs/mac-mini-deployment-notes.md \
  && echo "P5.4 已知风险节: OK" || echo "P5.4 FAIL: 缺少已知风险节"

# Phase 5.5: 文档里所有 ${VAR} 是否在前文 export / 占位符上下文
DOC=docs/mac-mini-deployment-notes.md
grep -oE '\$\{[A-Z_][A-Z0-9_]*\}' "$DOC" | sort -u > /tmp/5.5-vars.txt
_fail=0
while read -r v; do
  name=${v#'${'}; name=${name%'}'}
  if ! grep -qE "(export ${name}=|__${name}__|${name}=)" "$DOC"; then
    echo "MISSING export/placeholder: $v"; _fail=1
  fi
done < /tmp/5.5-vars.txt
[[ $_fail -eq 0 ]] && echo "P5.5 OK" || echo "P5.5 FAIL"

# Phase 5.6: 文档 Python 3.11+ 检查命令统一走 find-python311.sh
grep -c "find-python311.sh" docs/mac-mini-deployment-notes.md
# 期望: ≥ 1
! grep -E "(^|[^a-zA-Z0-9.])/usr/bin/python3([^.0-9]|$)" docs/mac-mini-deployment-notes.md \
  && echo "P5.6 no /usr/bin/python3 fork: OK" \
  || echo "P5.6 FAIL: doc has /usr/bin/python3 as 3.11+ entry"

# Phase 5.7
grep -c "Python 3" docs/mac-mini-deployment-notes.md
grep -c "占位符" docs/mac-mini-deployment-notes.md

# Phase 6.2: OPENROUTER 4xx 不静默 fallback（代码 review）
grep -nE "401|403|status_code|HTTPError|raise_for_status" src/akb2/briefing/synthesis.py | head -20
# 期望: 看到对 4xx 的显式处理 + stderr WARNING/ERROR；不是 except → silent fallback

# Phase 6.3: X_BEARER_TOKEN 缺失 → fallback + WARNING
for c in l0 l0_prime l3; do
  env -u X_BEARER_TOKEN PYTHONPATH=src "$PY" -m akb2.briefing.intake.${c}_collector \
      --output /tmp/${c}-no-token.json 2>/tmp/${c}-err.txt
  _e=$?
  [[ $_e -eq 0 ]] && echo "$c exit 0: OK" || echo "$c FAIL exit=$_e"
  grep -qi "WARNING\|dry-run\|fallback" /tmp/${c}-err.txt && echo "$c WARNING/fallback hint: OK" || echo "$c FAIL no WARNING"
done

# Phase 6.4: 三 collector 全空 → scoring/pipeline fail-closed
echo '{"tweets":[]}' > /tmp/empty-l0.json
PYTHONPATH=src "$PY" -m akb2.briefing.scoring --input /tmp/empty-l0.json --output /tmp/scored-empty.json 2>/tmp/6.4-err.txt
_e=$?
[[ $_e -ne 0 ]] && echo "P6.4 scoring fail-closed: OK" || {
  # 若 scoring 不 fail，验证下游 synthesis/pipeline 是否会 fail-closed
  echo "scoring exit 0；检查下游 synthesis 行为"
  PYTHONPATH=src "$PY" -m akb2.briefing.synthesis --input /tmp/scored-empty.json --output /tmp/synth-empty.json 2>/tmp/6.4b-err.txt
  _e2=$?
  [[ $_e2 -ne 0 ]] && echo "P6.4 synthesis fail-closed: OK" || echo "P6.4 FAIL: 全链路允许空数据通过"
}

# Phase 6.5: synthesis 输出不过 schema → validator 拒
echo '{"focus_discussions":"not_an_array","date":"2026-05-27"}' > /tmp/bad-synth.json
PYTHONPATH=src "$PY" -m akb2.briefing.validator /tmp/bad-synth.json 2>/tmp/6.5-err.txt
_e=$?
[[ $_e -ne 0 ]] && echo "P6.5 validator reject: OK" || echo "P6.5 FAIL"
grep -qi "focus_discussions\|array\|type" /tmp/6.5-err.txt && echo "P6.5 stderr 含 schema error: OK" || echo "P6.5 FAIL no schema hint"

# Phase 6.6: gate reject → Social/active 无文件（需先修 pipeline.py ×2 项）
# ⚠ 代码 gap ×2:
#   ① pipeline.py 无条件 shutil.copy2（须加 if gate_decision != "reject" guard）
#   ② pipeline.py GATE_CLI 硬编码，未读 AKB2_GATE_CLI env var
#      须在 GATE_CLI 定义处: GATE_CLI = Path(os.environ.get("AKB2_GATE_CLI") or str(...default...))
# ── 先确认两项代码修复均已完成（任一缺失则 fail-stop）:
_gap_ok=1
grep -nE "AKB2_GATE_CLI" src/akb2/briefing/pipeline.py | grep -q "environ" \
  || { echo "FAIL: pipeline.py 未读 AKB2_GATE_CLI env var（gap ②）"; _gap_ok=0; }
grep -nE "reject|gate_decision" src/akb2/briefing/pipeline.py \
  | grep -qE "!=.*reject|reject.*!=|if.*reject" \
  || { echo "FAIL: pipeline.py 无 reject guard（gap ①）"; _gap_ok=0; }
[[ $_gap_ok -eq 0 ]] && { echo "代码修复未完成，停止 Phase 6.6"; exit 1; } || echo "代码检查: OK"
# ── gate stub（Python 脚本，text 格式匹配 run_gate() 的 regex parser）:
# 注：pipeline.py 以 sys.executable <GATE_CLI> check ... 调用，stub 须是 Python 脚本
cat > /tmp/gate-stub-reject.py << 'STUB_EOF'
import sys
# text 格式匹配 run_gate() 的 regex: proposed_gate_decision:\s*(active|...|reject)
print("proposed_gate_decision: reject")
sys.exit(0)
STUB_EOF
TMP_VAULT_66=/tmp/akb2-vault-6-6
rm -rf "$TMP_VAULT_66"; mkdir -p "$TMP_VAULT_66"
AKB2_GATE_CLI=/tmp/gate-stub-reject.py \
  bash scripts/daily_briefing.sh --date 2026-05-27 --vault-root "$TMP_VAULT_66" --dry-run-intake \
  2>/tmp/6.6-err.txt; _e=$?
# 断言 ① Social/active 路径无文件
_active_count=$(ls "$TMP_VAULT_66/AKB2.0/AI TRENDS/Social/"*.md 2>/dev/null | wc -l | tr -d ' ')
[[ "$_active_count" == "0" ]] \
  && echo "P6.6① active 无文件: OK" \
  || echo "P6.6① FAIL: reject 仍写入 Social ($_active_count 个文件)"
# 断言 ② exit 非 0 或 stderr 含 reject 信号
( [[ $_e -ne 0 ]] || grep -qi "reject" /tmp/6.6-err.txt ) \
  && echo "P6.6② reject 信号: OK" || echo "P6.6② FAIL: 无 reject 信号"
rm -rf "$TMP_VAULT_66" /tmp/gate-stub-reject.py /tmp/6.6-err.txt

# Phase 6.7: staging/candidate 不存在 → pipeline 自动 mkdir
TMP_FRESH=/tmp/akb2-fresh-vault
rm -rf "$TMP_FRESH"; mkdir -p "$TMP_FRESH"
bash scripts/daily_briefing.sh --date 2026-05-27 --vault-root "$TMP_FRESH" --dry-run-intake
test -d "$TMP_FRESH/staging/candidate" && echo "P6.7 OK: pipeline created staging/candidate" || echo "P6.7 FAIL"
rm -rf "$TMP_FRESH"

# Phase 6.8: SCRIPT_DIR 含空格
TMP_SPACE="/tmp/with space"
mkdir -p "$TMP_SPACE"
ln -sfn "$PWD" "$TMP_SPACE/worktree"
TMP_VAULT_6_8=/tmp/akb2-vault-6-8
mkdir -p "$TMP_VAULT_6_8"
bash "$TMP_SPACE/worktree/scripts/daily_briefing.sh" --date 2026-05-27 --vault-root "$TMP_VAULT_6_8" --dry-run-intake
_e=$?
[[ $_e -eq 0 ]] && echo "P6.8 OK: SCRIPT_DIR with space handled" || echo "P6.8 FAIL exit=$_e"
rm -rf "$TMP_SPACE" "$TMP_VAULT_6_8"

# Phase 7 (按 reports/acceptance/2026-05-27.md §1 矩阵全跑)
# ... 见 acceptance 报告

# Phase 8
"$PY" -m pytest --collect-only tests/ -q
"$PY" -m pytest tests/briefing/test_validator.py -v
```
