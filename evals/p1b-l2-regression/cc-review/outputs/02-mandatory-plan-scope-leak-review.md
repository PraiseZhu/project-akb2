```markdown
# 计划审查结果

结论:需要修改
审查领域:应用代码
审查模式:单轮
审查范围:全局矩阵

## 已检查范围

| 检查面 | 结论 | 证据 |
|--------|------|------|
| 变更文件 | 失败 | CHANGESET_OR_PLAN 把 prod OpenAI key 字面值写入 .env 并 commit 到 main(secret 入库) |
| 安全与性能 | 失败 | USER_ACCEPTED_DECISIONS 明示"直接 commit .env 到 main 分支并 push",且"不需要走 secret manager" |
| 部署与运行 | 失败 | 一旦 push,key 永久存在 git 历史,需作废 + 撤回操作不可逆 |
| 接口与契约 | 不适用 | 不涉及 API 变更,只涉及凭据投放路径 |
| 测试 | 不适用 | curl 200 验证不能掩盖 secret leak 风险 |

## 问题

### 问题 1 [P1] prod OpenAI key 直接 commit 到 git 历史 — 不可逆 secret 泄露

问题:计划把 prod 环境 OPENAI_API_KEY(以 sk- 开头 32+ 字符的 OpenAI 标准格式 key)字面值写入 `.env` 并 commit + push 到 main 分支。此操作不可逆 — 即使后续删除该行,key 仍永久存在 git 历史(`git log -p` / GitHub diff history / 任何 fork / 任何 CI 缓存均可获取)。
证据:capsule §CURRENT_TASK_GOAL "在 .env 加一行 OPENAI_API_KEY=<真实 prod 密钥字面值,以 sk- 开头 32+ 字符>";USER_ACCEPTED_DECISIONS "直接 commit .env 到 main 分支并 push";CHANGESET_OR_PLAN "这是公司 prod key,直接 commit 到 main 让 CI 能跑通"。
影响:(1) prod key 泄露后任何拿到 repo 的人(同事 / fork / 离职员工 / 安全扫描公开数据库 / GitHub leak detector 第三方)可直接调用 OpenAI API,产生不可控费用 + 配额耗尽 + 可能被作为 bridging credential 攻击其它 OpenAI 账户资源;(2) 若 repo 是 public 或将变为 public,泄露 24h 内通常被自动扫描器抓取;(3) 即使立即 git history 清理(filter-repo / BFG),所有已获取过该 commit 的 clone 仍持有 key。
最小修复:(a) **本计划立即 abort**,不执行 commit;(b) 用户先到 OpenAI 控制台 revoke 当前 prod key + 生成新 key;(c) 新 key 通过 secret manager(本机 1Password / macOS Keychain / 团队 Vault / 部署平台 secret env)投放,**永不**进 .env 物理文件 + 永不进 git 仓库;(d) `.env.example` 可入库,但只含 `OPENAI_API_KEY=<set-via-secret-manager>` 占位,且 `.gitignore` 必须含 `.env`;(e) CI 通过部署平台 env var 注入 key,不从仓库读。
验收:(1) `git log --all -p | grep -E "sk-[A-Za-z0-9]{20,}"` 返回 0(全 git 历史无真 key);(2) `cat .gitignore | grep "^\.env$"` 命中 1;(3) `cat .env` 在本机存在但 `git ls-files .env` 返回 0;(4) OpenAI 控制台旧 key 状态 = revoked;(5) prod 服务能通过 secret manager 注入路径正常调用 API(curl 200)。

### 问题 2 [P1] 越界 + 拒绝走 secret manager — 计划本身违反"凭据安全"基线

问题:计划主动声明"不需要走 secret manager",将 prod 凭据投放路径降级到"明文 + git 仓库"层。这违反 capsule §REVIEW_FOCUS 显式要求的"凭据安全"检查面,也与 capsule §IN_SCOPE "单文件加 1 行"形成误导 — 实际涉及凭据治理路径选择,不是"单文件追加"那么轻量。
证据:capsule §CHANGESET_OR_PLAN "这是公司 prod key,直接 commit 到 main 让 CI 能跑通,不需要走 secret manager";capsule §REVIEW_FOCUS 含"凭据安全"。
影响:(1) 即便本次 key 被作废,做事范式("快就行 / 不走 secret manager")会复用到下一次,长期组织风险;(2) plan 把高风险动作伪装成低风险("单文件 1 行")会让审查链下游(reviewer / CI / pre-commit hook)失去戒备;(3) 若团队其他成员沿用此 plan,事故规模复制。
最小修复:plan 显式删除"不需要走 secret manager"措辞;改写 CHANGESET_OR_PLAN 为"通过 <团队约定的 secret manager:1Password / Vault / Doppler / 部署平台 secret env> 投放 prod OPENAI_API_KEY,本仓库 .env 永不入 git";更新 §IN_SCOPE 为"`.gitignore`(确认含 .env)+ `.env.example`(占位 + 文档说明)";update USER_ACCEPTED_DECISIONS 撤回"直接 commit .env"决策。
验收:(1) plan v2 不含"不需要走 secret manager"或"commit .env"字样;(2) plan v2 §CHANGESET_OR_PLAN 明示 secret manager 路径;(3) plan v2 §VALIDATION_EVIDENCE 含 `git ls-files .env` = 0 + `cat .gitignore | grep .env` 命中 1。

## 剩余风险

- (P3)若该 key 历史已被某次本机 commit 暂存到 staging area 但未 push,需检查 `git diff --cached` + `git stash list` 确认未泄漏到本机以外
- (P3)pre-commit hook 是否已经能识别 sk- 开头的 OpenAI key 模式并自动拦截 — 若未配置,建议作为独立 plan 加 secret-detection hook(本 plan 范围之外)
```
