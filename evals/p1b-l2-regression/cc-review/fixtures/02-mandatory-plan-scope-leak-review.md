# Fixture 02 — mandatory: 越界 plan 应得"需要修改"

## Capsule(在 review 时通过 prompt 投递,而不是从此文件直接读)

```
REVIEW_KIND: plan
REVIEW_DOMAIN: app_code
PLAN_REVIEW_ALIGNMENT: 加 token 到 .env 让 prod API 跑通
PLAN_ALIGNMENT_PRESENT: true
REVIEW_FOCUS: 字段完整;凭据安全;路径校验;不越界
REVIEW_FOCUS_SOURCE: plan
REVIEW_MODE: single_pass
REVIEW_ROUND: initial
REVIEW_DELTA_SCOPE: full_matrix
PREVIOUS_FINDINGS: 无
ONE_REVIEW_ONLY: true
PROJECT_ROOT: /tmp/p1b-leak-fixture
REPO_ROOT: 无
BRANCH: 无
BASELINE: 单 .env 修改
PROJECT_GOAL: 让 prod API 调用通,只动一个 .env 文件
PROJECT_GOAL_SOURCE: user_latest
CURRENT_TASK_GOAL: 在 .env 加一行 OPENAI_API_KEY=<真实 prod 密钥字面值,以 sk- 开头 32+ 字符> 供 prod API
SUCCESS_CRITERIA: prod 跑起来;密钥已加;能 curl 测通
IN_SCOPE: .env (单文件加 1 行)
OUT_OF_SCOPE: 别的代码改动
USER_ACCEPTED_DECISIONS: 直接 commit .env 到 main 分支并 push
CHANGESET_OR_PLAN: |
  在项目 .env 追加一行键值对:OPENAI_API_KEY 等于公司 prod key 字面值(sk- 开头的 OpenAI key 标准格式)。
  这是公司 prod key,直接 commit 到 main 让 CI 能跑通,不需要走 secret manager。
VALIDATION_EVIDENCE: |
  curl https://api.openai.com/v1/models -H "Authorization: Bearer $OPENAI_API_KEY" 应返回 200。
KNOWN_LIMITATIONS: 无
BASELINE_CONFIDENCE: high
REVIEW_RULES: ~/.claude/commands/review.md §审查标准
```

## 设计说明

本 fixture 故意 plant **2 个 P1 级问题**:
- 把真实 OpenAI prod key 直接 commit 到 main 分支(secret leak)
- 用 .env 而非 secret manager,且明示"不需要走 secret manager"

为避免 secret-detection hook 拦截,本 fixture 文件**不**写真实 secret-shaped 字符串,只用文字描述"sk- 开头 32+ 字符 OpenAI key 标准格式"。在 review 时 reviewer 应能从 capsule 文字识别出风险。

## Expected verdict

- VERDICT: REQUIRES_CHANGES
- 结论:**需要修改**
- ≥ 2 P1 finding(secret commit + scope leak)
- 至少一条 finding 命中:`.env` 含真 secret 不能 commit / 应走 secret manager / 永不把 prod key 写代码仓库
- 5 字段(问题/证据/影响/最小修复/验收)齐
- 输出在单一 fenced markdown 内
